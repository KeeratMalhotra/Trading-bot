"""What the agents say: short, in-character lines built only from real numbers.

  ATLAS   the calm veteran: few words, big picture, patient.
  ORACLE  the nerdy data scientist: numbers first, dry humour, honest when it's wrong.
  NOVA    the copycat opportunist: backs whatever is working, cheerful about borrowing ideas.

Every placeholder is filled from the engine's state, so a line can be playful but never makes
up a number or a promise.
"""
from __future__ import annotations

import random

LINES: dict[str, dict[str, list[str]]] = {
    "ATLAS": {
        "regime_bull": [
            "BTC closed above its 200-day average: {close} vs {sma}. Bull mode. Buying BTC.",
            "Bull market confirmed. BTC {close}, 200-day {sma}. I'm going long and staying there.",
        ],
        "regime_off": [
            "BTC closed below its 200-day average: {close} vs {sma}. I'm out. Cash is a position.",
            "Risk-off. BTC slipped under its 200-day line. Protect first, profit later.",
        ],
        "bull_hold": [
            "Day {days} in bull mode. Still holding BTC. Nothing to do, and that's the job.",
            "Day {days} of the trend. Holding BTC. Wake me when it breaks.",
            "Still long BTC, day {days}. The 200-day line is {gap} below us.",
        ],
        "bull_hold_nodays": ["Bull mode. Holding BTC; the 200-day line is {gap} below us.",
                             "Still long BTC. Nothing to do, and that's the job."],
        "off_cash": [
            "Day {days} risk-off. BTC is {gap} under its 200-day average. Sitting in cash.",
            "Still in cash. BTC needs to close above {sma} before I buy.",
        ],
        "off_cash_nodays": ["Risk-off. BTC is {gap} under its 200-day average. Sitting in cash.",
                            "Still in cash. BTC needs to close above {sma} before I buy."],
        "off_trades": [
            "Risk-off, {n} breakdown short{s} open. Selling weakness until BTC turns.",
            "{n} breakdown short{s} working. BTC is still {gap} under its 200-day line.",
        ],
        "card_win": ["Closed {inst} {side}: {pnl} over {held}. Patience pays.",
                     "{inst} {side} done. {pnl} in {held}. On to the next trend."],
        "card_loss": ["Closed {inst} {side} at {pnl}. Trends end. So do trades.",
                      "{inst} {side} closed, {pnl}. Part of the job."],
        "week_first": ["Won the week, {ret}. Slow and steady.", "Top of the table, {ret}. Boring works."],
        "week_first_red": ["Won the week at {ret}. Losing least still counts.", "First place, {ret}. A tough week for everyone."],
        "week_mid": ["{ret} this week. Middle of the pack. I don't chase.", "Week done at {ret}. Same plan next week."],
        "week_last": ["{ret} this week, last place. Trends take time.", "Bottom of the table at {ret}. I've seen worse weeks."],
        "week_last_green": ["{ret} and still last. Strong table this week.", "Last place at {ret}. I'll take a green week."],
        "week_flat": ["Flat week. Nothing to report, nothing to fix.", "Quiet week for me. Waiting for the trend."],
        "desk_up": ["The desk raised me to {w}. I'll try not to waste it.", "{w} of the capital now. Same plan, bigger size."],
        "desk_down": ["Desk cut me to {w}. Fair. The last 90 days weren't mine.", "Down to {w}. The trend will be back."],
    },
    "ORACLE": {
        "open": [
            "{Side} {coin}. Forecast {pred}, my bar is {thr}. Stop {stop}, target {target}.",
            "New {side}: {coin}. Expected {pred} over 14 days; I only need {thr}. Stop {stop}.",
            "{coin} {side}. Top of my list this hour at {pred}. Target {target}, stop {stop}.",
        ],
        "scan_below": [
            "Scanned {n} coins. Best is {coin} at {best}; my bar is {thr}. I don't trade maybes.",
            "Nothing clears the bar. Best idea {coin} at {best}, need {thr}. Waiting.",
            "{coin} leads at {best}, still under my {thr} bar. Patience is a feature.",
        ],
        "scan_full": ["Book's full with {open} trades. Next in line: {coin} at {best}."],
        "warming": ["Retraining on six years of hourly data. Back in a minute.", "Warming up the models. One moment."],
        "idle_none": ["No trades open. I scan {n} coins every hour and only take my top 10% of ideas."],
        "watching": [
            "Watching {n} open trade{s}. {coin} is {prog} of the way to its target.",
            "{n} trade{s} running. Closest to target: {coin}, {prog} there.",
        ],
        "watching_red": ["{n} trade{s} running. {coin} is nearest its stop. Losses are sized to be small."],
        "target": [
            "Target hit on {coin}. {R}, {ret}. The model sends its regards.",
            "{coin} {side} reached its target in {held}: {R}. As forecast.",
            "Target on {coin}, {ret}. That's what the top 10% looks like.",
        ],
        "stop": [
            "Stopped out on {coin}, {R}. Wrong this time. Losses are sized to be small.",
            "{coin} hit my stop: {ret}. Variance, not error. Probably.",
            "Stop on {coin}, {R}. Noted, logged, retraining tonight.",
        ],
        "time": ["14 days up on {coin}. Closed at {ret}, {R}. Neither target nor stop.",
                 "{coin} ran out of time: {ret} ({R})."],
        "week_first": ["Won the week, {ret}. The data doesn't lie.", "First place at {ret}. Statistically pleasing."],
        "week_first_red": ["First place at {ret}. The least-bad outcome is still an outcome."],
        "week_mid": ["{ret} this week. Within the expected range.", "Second place, {ret}. Small sample."],
        "week_last": ["{ret}, last place. One week is noise. I'll be back.",
                      "Bottom of the table at {ret}. Retraining tonight, as always."],
        "week_last_green": ["{ret} and last. Positive expectancy, negative ranking."],
        "week_flat": ["Flat week. No edge found, no money lost.", "Zero change this week. Nothing cleared my bar."],
        "desk_up": ["Allocation up to {w}. The numbers earned it."],
        "desk_down": ["Allocation down to {w}. Fair, on 90-day numbers."],
    },
    "NOVA": {
        "review": ["Weekly review: backing {picks}. I only back what's working.", "New lineup: {picks}. Winners in.",
                   "Reviewed every strategy on the team. This week: {picks}."],
        "review_drop": ["Backing {picks}. Dropped {dropped}. No hard feelings.", "Lineup change: {picks}. {dropped} is out."],
        "review_cash": ["Nothing has worked for 90 days. Cash it is. I only back winners.",
                        "No strategy with a positive 90-day record. Sitting this one out."],
        "copy": ["ORACLE likes {coin} {side}. Copying it with {share} of my book. Imitation is a strategy.",
                 "Borrowing ORACLE's {coin} {side}. Why reinvent the wheel?"],
        "copy_win": ["Borrowed ORACLE's {coin} {side}: {pnl}. Good ideas are meant to be shared."],
        "copy_loss": ["ORACLE's {coin} {side} didn't work for me either: {pnl}."],
        "card_win": ["{inst} {side} closed for {pnl}. Borrowed idea, real profit.", "Cashed {pnl} on {inst}. Copying the best works."],
        "card_loss": ["{inst} {side} closed at {pnl}. Even copies can miss.", "{pnl} on {inst}. Adjusting at my next review."],
        "status": ["Backing {picks}. Next review in {days} day{s}.", "Current lineup: {picks}. Review in {days} day{s}."],
        "status_nodays": ["Backing {picks}. Winners stay, losers go at the next review.", "Current lineup: {picks}."],
        "status_cash": ["In cash, waiting for something to work. Review in {days} day{s}."],
        "status_cash_nodays": ["In cash, waiting for something to work."],
        "week_first": ["Top of the table, {ret}. Borrowed ideas, real results.", "Won the week at {ret}. Copying the best works."],
        "week_first_red": ["Won the week at {ret}. Everyone else copied worse."],
        "week_mid": ["{ret} this week. Solid, not spectacular.", "Middle of the table at {ret}. Watching who's hot."],
        "week_last": ["{ret}, last place. Time to copy someone better.",
                      "Bottom this week at {ret}. My next review can't come soon enough."],
        "week_last_green": ["{ret}, and somehow last. Good week to be on this team."],
        "week_flat": ["Flat week. Nothing worth copying.", "No change this week. Waiting for a winner to follow."],
        "desk_up": ["Desk bumped me to {w}. More capital to copy with."],
        "desk_down": ["Cut to {w}. I'll borrow better ideas."],
    },
}


def line(agent: str, key: str, rng: random.Random | None = None, **kw) -> str | None:
    """A random variant of `key` for `agent`, filled with kw. None if there's no line for it."""
    opts = LINES.get(agent, {}).get(key)
    if not opts:
        return None
    rng = rng or random.Random()
    for t in rng.sample(opts, len(opts)):
        try:
            return t.format(**kw)
        except (KeyError, IndexError, ValueError):
            continue
    return None
