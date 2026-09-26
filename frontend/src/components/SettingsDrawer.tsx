import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Pause, Play, RotateCcw, X } from "lucide-react";
import clsx from "clsx";
import { useStore } from "../store";
import { pct, usd } from "../lib/format";

export function SettingsDrawer() {
  const open = useStore((s) => s.settingsOpen);
  const setS = useStore((s) => s.set);
  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setS({ settingsOpen: false })}
          />
          <motion.aside
            className="fixed right-0 top-0 bottom-0 z-50 w-[420px] max-w-full bg-ink-900 border-l border-white/10 overflow-y-auto scroll-thin"
            initial={{ x: 440 }}
            animate={{ x: 0 }}
            exit={{ x: 440 }}
            transition={{ type: "spring", stiffness: 320, damping: 34 }}
          >
            <Body />
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

function Body() {
  const meta = useStore((s) => s.meta);
  const bots = useStore((s) => s.live?.bots ?? []);
  const token = useStore((s) => s.adminToken);
  const setS = useStore((s) => s.set);
  const api = useStore((s) => s.api);
  const [auth, setAuth] = useState<{ required: boolean; ok: boolean } | null>(null);
  const [msg, setMsg] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const [income, setIncome] = useState(String(meta?.tax.other_income ?? 75000));

  useEffect(() => {
    api("/api/auth").then((r) => r.json()).then(setAuth);
  }, [api, token]);

  async function save(body: Record<string, unknown>) {
    const r = await api("/api/settings", body);
    if (r.ok) {
      setS({ meta: await r.json() });
      setMsg("Saved ✓");
    } else setMsg(r.status === 401 ? "Admin token required" : "Failed to save");
    setTimeout(() => setMsg(""), 2500);
  }

  async function botAction(id: string, action: "pause" | "resume") {
    const r = await api(`/api/bots/${id}/${action}`, {});
    if (!r.ok) setMsg("Admin token required");
  }

  async function reset() {
    const r = await api("/api/reset", {});
    setConfirmReset(false);
    if (r.ok) setTimeout(() => location.reload(), 500);
    else setMsg("Admin token required");
  }

  if (!meta) return null;
  const locked = auth?.required && !auth.ok;

  return (
    <div className="p-5 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-lg font-semibold text-white">Settings</div>
          <div className="text-xs text-ink-400">Operator controls. Viewers only see the dashboard.</div>
        </div>
        <button onClick={() => setS({ settingsOpen: false })} className="p-2 rounded-lg hover:bg-white/5 text-ink-300">
          <X className="w-4 h-4" />
        </button>
      </div>
      {msg && <div className="text-xs text-up">{msg}</div>}

      {auth?.required && (
        <Section title="Admin token">
          <input
            type="password"
            value={token}
            onChange={(e) => setS({ adminToken: e.target.value })}
            placeholder="ADMIN_TOKEN from the server"
            className="w-full bg-ink-800 border border-white/10 rounded-lg px-3 py-2 text-sm"
          />
          <div className={clsx("text-[11px] mt-1", auth.ok ? "text-up" : "text-ink-400")}>{auth.ok ? "Unlocked" : "Locked: controls are read-only"}</div>
        </Section>
      )}

      <Section title="US tax profile" hint="Used to estimate tax on every closed trade. Estimates only, not tax advice.">
        <div className="grid grid-cols-2 gap-2">
          {(["single", "mfj"] as const).map((fs) => (
            <button
              key={fs}
              disabled={locked}
              onClick={() => save({ filing_status: fs })}
              className={clsx(
                "rounded-lg px-3 py-2 text-sm border",
                meta.tax.filing_status === fs ? "border-white/30 bg-white/10 text-white" : "border-white/10 text-ink-300",
              )}
            >
              {fs === "single" ? "Single" : "Married, joint"}
            </button>
          ))}
        </div>
        <label className="block text-xs text-ink-400 mt-3 mb-1">Other yearly income (sets your bracket)</label>
        <div className="flex gap-2">
          <input
            value={income}
            disabled={locked}
            onChange={(e) => setIncome(e.target.value.replace(/[^0-9]/g, ""))}
            className="flex-1 bg-ink-800 border border-white/10 rounded-lg px-3 py-2 text-sm num"
          />
          <button disabled={locked} onClick={() => save({ other_income: Number(income) })} className="px-3 rounded-lg bg-white/10 text-sm text-white">
            Save
          </button>
        </div>
        <label className="block text-xs text-ink-400 mt-3 mb-1">State</label>
        <select
          value={meta.tax.state}
          disabled={locked}
          onChange={(e) => save({ state: e.target.value })}
          className="w-full bg-ink-800 border border-white/10 rounded-lg px-3 py-2 text-sm"
        >
          {meta.states.map((s) => (
            <option key={s.code} value={s.code}>
              {s.name} {s.code !== "XX" ? `(${pct(s.rate, 2, false)})` : ""}
            </option>
          ))}
        </select>
      </Section>

      <Section title="Exchange fee tier" hint="Coinbase Advanced maker/taker fees applied to every simulated fill. Auto = tier from the arena's own 30-day volume (starts at the expensive Intro tier).">
        <select
          value={meta.fees.mode}
          disabled={locked}
          onChange={(e) => save({ fee_mode: e.target.value })}
          className="w-full bg-ink-800 border border-white/10 rounded-lg px-3 py-2 text-sm"
        >
          <option value="auto">Auto (based on 30-day volume)</option>
          {meta.fees.tiers?.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name}: {pct(t.maker, 2, false)} / {pct(t.taker, 2, false)} ({usd(t.min, { compact: true })}+ volume)
            </option>
          ))}
        </select>
      </Section>

      <Section title="Bots">
        <div className="space-y-2">
          {bots.map((b) => (
            <div key={b.id} className="flex items-center gap-3 rounded-lg bg-white/[0.03] px-3 py-2">
              <span className="w-2 h-2 rounded-full" style={{ background: b.color }} />
              <div className="flex-1 leading-tight">
                <div className="text-sm font-semibold text-white">{b.name}</div>
                <div className="text-[11px] text-ink-400">
                  {pct(b.profile.risk_per_trade, 1, false)} risk/trade · {b.profile.signal_tf}/{b.profile.trend_tf} · {b.profile.symbols.length} coins ·{" "}
                  {b.profile.entry_order} entries
                </div>
              </div>
              <button
                disabled={locked}
                onClick={() => botAction(b.id, b.paused ? "resume" : "pause")}
                className="p-2 rounded-lg bg-white/5 hover:bg-white/10 text-ink-200"
                title={b.paused ? "Resume" : "Pause new entries"}
              >
                {b.paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              </button>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Danger zone">
        {!confirmReset ? (
          <button
            disabled={locked}
            onClick={() => setConfirmReset(true)}
            className="flex items-center gap-2 rounded-lg border border-down/30 text-down px-3 py-2 text-sm hover:bg-down/10"
          >
            <RotateCcw className="w-4 h-4" /> Reset the battle
          </button>
        ) : (
          <div className="rounded-lg border border-down/30 p-3 text-sm">
            <div className="text-white">Close everything and restart all bots at {usd(meta.starting_balance)}?</div>
            <div className="flex gap-2 mt-2">
              <button onClick={reset} className="px-3 py-1.5 rounded-lg bg-down text-white text-sm font-semibold">
                Yes, reset
              </button>
              <button onClick={() => setConfirmReset(false)} className="px-3 py-1.5 rounded-lg bg-white/10 text-sm">
                Cancel
              </button>
            </div>
          </div>
        )}
      </Section>

      <div className="text-[11px] text-ink-400 leading-relaxed">
        Mode: <span className="text-sky-300 font-semibold">{meta.mode.toUpperCase()}</span>. All balances are demo money. Prices are live from Coinbase; fills,
        fees, slippage and taxes are simulated to match reality as closely as possible. Not financial advice.
      </div>
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="panel-title mb-1">{title}</div>
      {hint && <div className="text-[11px] text-ink-400 mb-2 leading-snug">{hint}</div>}
      {children}
    </div>
  );
}
