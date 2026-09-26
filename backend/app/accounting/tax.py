"""US tax estimate for crypto trading gains (tax year 2026).

Crypto is property (IRS Notice 2014-21): every sell is a taxable disposal.
  * held <= 1 year  -> short-term gain, taxed as ordinary income (10%-37%)
  * held  > 1 year  -> long-term gain, 0% / 15% / 20%
  * 3.8% Net Investment Income Tax above $200k (single) / $250k (MFJ) MAGI
  * net capital losses offset up to $3,000 of ordinary income per year
  * wash-sale rule (IRC 1091) does not currently apply to crypto
  * state tax: approximate marginal rate for the chosen state (editable)

Source for 2026 federal numbers: IRS IR-2025-103 / Rev. Proc. 2025-32.
This is an ESTIMATE for entertainment/education, not tax advice.
"""
from __future__ import annotations

from dataclasses import dataclass

INF = float("inf")

FED_BRACKETS = {
    "single": [(12_400, .10), (50_400, .12), (105_700, .22), (201_775, .24), (256_225, .32), (640_600, .35), (INF, .37)],
    "mfj": [(24_800, .10), (100_800, .12), (211_400, .22), (403_550, .24), (512_450, .32), (768_700, .35), (INF, .37)],
}
STD_DEDUCTION = {"single": 16_100, "mfj": 32_200}
LTCG_BREAKS = {"single": (49_450, 545_500), "mfj": (98_900, 613_700)}  # 0% up to, 15% up to
NIIT_THRESHOLD = {"single": 200_000, "mfj": 250_000}
CAPITAL_LOSS_LIMIT = 3_000

# Approximate marginal state income-tax rate on gains for a ~$75k-$150k earner (2026).
# Many states are progressive; users can override with a custom rate in settings.
STATE_RATES: dict[str, tuple[str, float]] = {
    "XX": ("Federal only", 0.0),
    "AL": ("Alabama", .050), "AK": ("Alaska", 0.0), "AZ": ("Arizona", .025), "AR": ("Arkansas", .039),
    "CA": ("California", .093), "CO": ("Colorado", .044), "CT": ("Connecticut", .055), "DE": ("Delaware", .066),
    "DC": ("Washington DC", .085), "FL": ("Florida", 0.0), "GA": ("Georgia", .0519), "HI": ("Hawaii", .079),
    "ID": ("Idaho", .053), "IL": ("Illinois", .0495), "IN": ("Indiana", .030), "IA": ("Iowa", .038),
    "KS": ("Kansas", .0558), "KY": ("Kentucky", .035), "LA": ("Louisiana", .030), "ME": ("Maine", .0675),
    "MD": ("Maryland", .0475), "MA": ("Massachusetts", .085), "MI": ("Michigan", .0425), "MN": ("Minnesota", .068),
    "MS": ("Mississippi", .040), "MO": ("Missouri", .047), "MT": ("Montana", .059), "NE": ("Nebraska", .052),
    "NV": ("Nevada", 0.0), "NH": ("New Hampshire", 0.0), "NJ": ("New Jersey", .05525), "NM": ("New Mexico", .049),
    "NY": ("New York", .060), "NC": ("North Carolina", .0399), "ND": ("North Dakota", .0195), "OH": ("Ohio", .0275),
    "OK": ("Oklahoma", .0475), "OR": ("Oregon", .0875), "PA": ("Pennsylvania", .0307), "RI": ("Rhode Island", .0475),
    "SC": ("South Carolina", .062), "SD": ("South Dakota", 0.0), "TN": ("Tennessee", 0.0), "TX": ("Texas", 0.0),
    "UT": ("Utah", .045), "VT": ("Vermont", .066), "VA": ("Virginia", .0575), "WA": ("Washington", 0.0),
    "WV": ("West Virginia", .048), "WI": ("Wisconsin", .053), "WY": ("Wyoming", 0.0),
}


@dataclass
class TaxSettings:
    filing_status: str = "single"   # single | mfj
    other_income: float = 75_000    # wages etc. - determines your bracket
    state: str = "XX"
    state_rate_override: float | None = None

    @property
    def state_rate(self) -> float:
        if self.state_rate_override is not None:
            return self.state_rate_override
        return STATE_RATES.get(self.state, ("", 0.0))[1]

    def to_json(self) -> dict:
        return {"filing_status": self.filing_status, "other_income": self.other_income,
                "state": self.state, "state_name": STATE_RATES.get(self.state, ("Custom", 0))[0],
                "state_rate": self.state_rate, "state_rate_override": self.state_rate_override}


def _bracket_tax(taxable: float, brackets) -> float:
    tax, lo = 0.0, 0.0
    for hi, rate in brackets:
        if taxable <= lo:
            break
        tax += (min(taxable, hi) - lo) * rate
        lo = hi
    return tax


def _marginal(taxable: float, brackets) -> float:
    for hi, rate in brackets:
        if taxable <= hi:
            return rate
    return brackets[-1][1]


def _total(s: TaxSettings, st: float, lt: float) -> dict:
    fs = s.filing_status if s.filing_status in FED_BRACKETS else "single"
    # netting short vs long term
    net = st + lt
    if net < 0:
        st_eff, lt_eff = max(net, -CAPITAL_LOSS_LIMIT), 0.0
    elif st < 0:
        st_eff, lt_eff = 0.0, lt + st
    elif lt < 0:
        st_eff, lt_eff = st + lt, 0.0
    else:
        st_eff, lt_eff = st, lt
    ordinary_taxable = max(0.0, s.other_income + st_eff - STD_DEDUCTION[fs])
    fed_ord = _bracket_tax(ordinary_taxable, FED_BRACKETS[fs])
    # long-term gains stack on top of ordinary income
    b0, b15 = LTCG_BREAKS[fs]
    start, end = ordinary_taxable, ordinary_taxable + max(lt_eff, 0.0)
    lt_tax, lo = 0.0, 0.0
    for hi, rate in ((b0, 0.0), (b15, .15), (INF, .20)):
        lt_tax += max(0.0, min(end, hi) - max(start, lo)) * rate
        lo = hi
    magi = s.other_income + st_eff + lt_eff
    nii = max(st_eff, 0) + max(lt_eff, 0)
    niit = 0.038 * min(nii, max(0.0, magi - NIIT_THRESHOLD[fs]))
    state = s.state_rate * (st_eff + lt_eff)
    return {"federal": fed_ord + lt_tax, "niit": niit, "state": state,
            "marginal_federal": _marginal(ordinary_taxable, FED_BRACKETS[fs])}


def estimate(s: TaxSettings, st_gain: float, lt_gain: float = 0.0) -> dict:
    """Incremental tax caused by the trading gains (vs. your other income alone)."""
    base = _total(s, 0.0, 0.0)
    withg = _total(s, st_gain, lt_gain)
    fed = withg["federal"] - base["federal"]
    niit = withg["niit"] - base["niit"]
    state = withg["state"] - base["state"]
    total = fed + niit + state
    gains = st_gain + lt_gain
    return {"federal": fed, "niit": niit, "state": state, "total": total,
            "effective_rate": (total / gains) if gains > 0 else 0.0,
            "marginal_federal": withg["marginal_federal"], "state_rate": s.state_rate,
            "short_term": st_gain, "long_term": lt_gain,
            "loss_carryforward": max(0.0, -(st_gain + lt_gain) - CAPITAL_LOSS_LIMIT)}
