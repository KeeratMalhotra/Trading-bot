"""Coinbase Advanced Trade live execution adapter - FUTURE REAL-MONEY MODE.

NOT ENABLED. The arena only builds this venue when ALL of these are true:
  TRADING_MODE=live
  LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_IS_AT_RISK
  COINBASE_API_KEY_NAME / COINBASE_API_PRIVATE_KEY set (CDP "trade" key, NO withdraw permission)

It implements the same ExecutionVenue interface as the paper exchange, so the bots,
risk manager and UI work unchanged. Before using real money: test with tiny size,
keep LIVE_MAX_ORDER_USD low, and never create an API key with withdrawal rights.

Status: written against the public Advanced Trade REST docs, not yet exercised
against a funded account. Treat as a starting point that needs verification.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import secrets
import time

import httpx

from .base import ExecutionVenue, Fill, Order, OrderUpdate

log = logging.getLogger("execution.coinbase_live")
API_HOST = "api.coinbase.com"
ACK_PHRASE = "I_UNDERSTAND_REAL_MONEY_IS_AT_RISK"


def live_enabled() -> bool:
    return (os.getenv("TRADING_MODE") == "live" and os.getenv("LIVE_TRADING_ACK") == ACK_PHRASE
            and bool(os.getenv("COINBASE_API_KEY_NAME")) and bool(os.getenv("COINBASE_API_PRIVATE_KEY")))


class CoinbaseLiveVenue(ExecutionVenue):
    mode = "live"

    def __init__(self, key_name: str, private_key_pem: str, max_order_usd: float = 50.0):
        self.key_name = key_name
        self.private_key = private_key_pem.replace("\\n", "\n")
        self.max_order_usd = max_order_usd
        self.client = httpx.AsyncClient(base_url=f"https://{API_HOST}", timeout=10)
        self.orders: dict[str, Order] = {}         # our id -> order
        self.exchange_ids: dict[str, str] = {}      # our id -> coinbase order id
        self.increments: dict[str, tuple[float, float]] = {}
        self._updates: list[OrderUpdate] = []
        self._maker, self._taker = 0.006, 0.012     # refreshed from /transaction_summary
        self.killed = False

    # --------------------------------------------------------------- auth
    def _jwt(self, method: str, path: str) -> str:
        import jwt  # PyJWT
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_private_key(self.private_key.encode(), password=None)
        now = int(time.time())
        payload = {"sub": self.key_name, "iss": "cdp", "nbf": now, "exp": now + 120,
                   "uri": f"{method} {API_HOST}{path.split('?')[0]}"}
        return jwt.encode(payload, key, algorithm="ES256",
                          headers={"kid": self.key_name, "nonce": secrets.token_hex()})

    async def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._jwt(method, path)}", "Content-Type": "application/json"}
        r = await self.client.request(method, path, json=body, headers=headers)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------- setup
    async def start(self, symbols: list[str]) -> None:
        for s in symbols:
            p = await self._req("GET", f"/api/v3/brokerage/products/{s}")
            self.increments[s] = (float(p["base_increment"]), float(p["quote_increment"]))
        await self.refresh_fees()
        asyncio.create_task(self._poll_loop())

    async def refresh_fees(self) -> None:
        d = await self._req("GET", "/api/v3/brokerage/transaction_summary")
        ft = d.get("fee_tier", {})
        self._maker = float(ft.get("maker_fee_rate", self._maker))
        self._taker = float(ft.get("taker_fee_rate", self._taker))

    async def balances(self) -> dict[str, float]:
        d = await self._req("GET", "/api/v3/brokerage/accounts?limit=250")
        return {a["currency"]: float(a["available_balance"]["value"]) for a in d.get("accounts", [])}

    # --------------------------------------------------------------- api
    def fee_rates(self, now: float) -> tuple[float, float]:
        return self._maker, self._taker

    def submit(self, order: Order, now: float) -> None:
        order.created_ts = now
        notional = order.qty * (order.limit_price or order.ref_price)
        if self.killed:
            return self._reject(order, "kill switch engaged")
        if order.side == "buy" and notional > self.max_order_usd:
            return self._reject(order, f"order ${notional:.2f} exceeds LIVE_MAX_ORDER_USD ${self.max_order_usd:.2f}")
        order.status = "open"
        self.orders[order.id] = order
        asyncio.create_task(self._place(order))

    def cancel(self, order_id: str, now: float, reason: str = "") -> None:
        o = self.orders.get(order_id)
        if o:
            o.reason = reason
            asyncio.create_task(self._cancel(order_id))

    def step(self, now: float) -> list[OrderUpdate]:
        out, self._updates = self._updates, []
        return out

    def kill(self) -> None:
        """Emergency stop: cancel every open order and refuse new ones."""
        self.killed = True
        for oid in list(self.orders):
            asyncio.create_task(self._cancel(oid))

    # ---------------------------------------------------------- internals
    def _reject(self, order: Order, reason: str) -> None:
        order.status, order.reason = "rejected", reason
        self._updates.append(OrderUpdate(order, "rejected", done=True))

    def _round(self, symbol: str, qty: float, price: float | None) -> tuple[str, str | None]:
        bi, qi = self.increments.get(symbol, (1e-8, 0.01))
        q = math.floor(qty / bi) * bi
        p = None if price is None else round(round(price / qi) * qi, 10)
        return f"{q:.10f}".rstrip("0").rstrip("."), (None if p is None else f"{p:.10f}".rstrip("0").rstrip("."))

    async def _place(self, o: Order) -> None:
        base, price = self._round(o.symbol, o.qty, o.limit_price)
        if o.type == "market":
            cfg = {"market_market_ioc": {"base_size": base}}
        else:
            cfg = {"limit_limit_gtc": {"base_size": base, "limit_price": price, "post_only": o.post_only}}
        body = {"client_order_id": o.id, "product_id": o.symbol, "side": o.side.upper(), "order_configuration": cfg}
        try:
            res = await self._req("POST", "/api/v3/brokerage/orders", body)
            if not res.get("success"):
                self.orders.pop(o.id, None)
                return self._reject(o, str(res.get("error_response", res)))
            self.exchange_ids[o.id] = res["success_response"]["order_id"]
        except Exception as e:  # noqa: BLE001
            self.orders.pop(o.id, None)
            self._reject(o, f"submit failed: {e}")

    async def _cancel(self, order_id: str) -> None:
        xid = self.exchange_ids.get(order_id)
        if xid:
            try:
                await self._req("POST", "/api/v3/brokerage/orders/batch_cancel", {"order_ids": [xid]})
            except Exception as e:  # noqa: BLE001
                log.warning("cancel failed %s: %s", order_id, e)

    async def _poll_loop(self) -> None:
        while True:
            for oid, o in list(self.orders.items()):
                xid = self.exchange_ids.get(oid)
                if not xid:
                    continue
                try:
                    d = (await self._req("GET", f"/api/v3/brokerage/orders/historical/{xid}"))["order"]
                except Exception as e:  # noqa: BLE001
                    log.warning("poll failed %s: %s", oid, e)
                    continue
                filled = float(d.get("filled_size") or 0)
                fees = float(d.get("total_fees") or 0)
                status = d.get("status")
                new_qty = filled - o.filled_qty
                if new_qty > 1e-12:
                    avg = float(d.get("average_filled_price") or 0)
                    # price of the incremental slice
                    px = (avg * filled - o.avg_price * o.filled_qty) / new_qty if o.filled_qty else avg
                    fee = fees - o.fees
                    o.avg_price, o.filled_qty, o.fees = avg, filled, fees
                    done = status in ("FILLED", "CANCELLED", "EXPIRED", "FAILED") and filled >= o.qty * 0.999
                    liq = "maker" if o.type == "limit" else "taker"
                    self._updates.append(OrderUpdate(o, "fill", Fill(new_qty, px, fee, liq, time.time()), done=done))
                    if done:
                        o.status = "filled"
                        self.orders.pop(oid, None)
                        continue
                if status in ("CANCELLED", "EXPIRED", "FAILED"):
                    o.status = "cancelled"
                    self.orders.pop(oid, None)
                    self._updates.append(OrderUpdate(o, "cancelled", done=True))
                elif status == "FILLED" and oid in self.orders:
                    o.status = "filled"
                    self.orders.pop(oid, None)
            await asyncio.sleep(1.0)
