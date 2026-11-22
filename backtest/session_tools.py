#!/usr/bin/env python3
"""
Session tools for the daily One-Agent loop (see CLAUDE.md). Three commands:

  rules      Print today's rule numbers, DERIVED from the config (never hand-typed):
             max opening-range width, limit-order offset, target multiple.
                 python backtest/session_tools.py rules

  status     Read the paper/live trade log and say GO / PAUSE / STOP / FIX-PROOF,
             applying the playbook §2.2 escalation rules mechanically.
                 python backtest/session_tools.py status --log logs/trade_log_2026.csv

  reconcile  Re-run the backtest engine on one session's 1-minute bars and compare
             it with what you logged. This is the core Proof step: it separates
             "I broke a rule" from "the fill model differs" from "the rule is ambiguous".
                 python backtest/session_tools.py reconcile --log logs/trade_log_2026.csv \
                     --bars data/session_2026-11-23.csv --data-tz America/Chicago --date 2026-11-23

All three read toolbox/backtest_config.json by default.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orb_backtest as ob  # noqa: E402

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "toolbox" / "backtest_config.json"
SIM_ONLY_SESSIONS_AFTER_VIOLATION = 5          # playbook §2.2


# --------------------------------------------------------------------------- rules
def rules(cfg: ob.Config) -> dict:
    n = ob.derived_numbers(cfg)
    return {
        "opening_range": f"{cfg.or_start}-{cfg.or_end} ET (all 1-min bars)",
        "max_or_width_pts": n["max_or_width_pts"],
        "precheck_formula": f"(width + {n['precheck_extra_pts']}) x ${cfg.point_value:g} + "
                            f"${n['round_trip_commission_usd']:g} <= ${cfg.max_risk_usd:g}",
        "min_or_width_pts": cfg.min_or_points,
        "signal": f"first completed {cfg.signal_minutes}-min close outside the range, "
                  f"{cfg.entry_start}-{cfg.entry_end} ET",
        "limit_offset_pts": n["limit_offset_pts"],
        "long_limit": f"OR Low + {n['limit_offset_pts']}",
        "short_limit": f"OR High - {n['limit_offset_pts']}",
        "stop": "opposite OR boundary",
        "target": f"entry +/- {cfg.target_r} x |entry - stop|, rounded away from entry",
        "time_exit": f"{cfg.exit_time} ET",
        "daily_loss_limit_usd": cfg.daily_loss_limit_usd,
        "drawdown_pause_usd": cfg.drawdown_pause_usd,
        "drawdown_stop_usd": cfg.drawdown_stop_usd,
    }


# --------------------------------------------------------------------------- status
def _bool(x) -> bool:
    return str(x).strip().lower() in ("true", "1", "y", "yes")


def _blank(x) -> bool:
    return pd.isna(x) or str(x).strip() == ""


def status(log: pd.DataFrame, cfg: ob.Config, mode: str = "SIM") -> dict:
    log = log[log["mode"].astype(str).str.upper() == mode.upper()].copy()
    log = log.sort_values("date", kind="stable").reset_index(drop=True)
    if log.empty:
        return {"mode": mode, "sessions": 0, "verdict": "GO", "reasons": ["no sessions logged yet"]}
    log["traded_b"] = log["traded"].map(_bool)
    log["net"] = pd.to_numeric(log["net_usd"], errors="coerce").fillna(0.0)
    trades = log[log["traded_b"]]
    eq = cfg.starting_capital + trades["net"].cumsum().to_numpy()
    peak = np.maximum.accumulate(np.r_[cfg.starting_capital, eq])[1:] if len(eq) else np.array([])
    dd_now = float(peak[-1] - eq[-1]) if len(eq) else 0.0
    equity = float(eq[-1]) if len(eq) else cfg.starting_capital

    losing_streak = 0
    for v in trades["net"].to_numpy()[::-1]:
        if v > 0:
            break
        losing_streak += 1
    no_trade_streak = 0
    for t in log["traded_b"].to_numpy()[::-1]:
        if t:
            break
        no_trade_streak += 1

    viol = np.flatnonzero(log["rule_violation"].map(_bool).to_numpy())
    sim_only_left = 0
    if viol.size:
        since = len(log) - 1 - int(viol[-1])
        sim_only_left = max(0, SIM_ONLY_SESSIONS_AFTER_VIOLATION - since)

    known = set(ob.REASON_CODES) | set(ob.LIVE_ONLY_REASON_CODES)
    proof_gaps = []
    for _, r in log.iterrows():
        missing = []
        if _blank(r.get("rule_violation")):
            missing.append("rule_violation")
        if r["traded_b"]:
            missing += [c for c in ("entry_price", "stop_price", "exit_price", "exit_reason", "net_usd",
                                    "chart_screenshot_path") if _blank(r.get(c))]
        elif _blank(r.get("no_trade_reason")) or str(r.get("no_trade_reason")) not in known:
            missing.append("no_trade_reason (must be a playbook §2.1 code)")
        if missing:
            proof_gaps.append(f"{r['date']}: {', '.join(missing)}")

    last_day_net = float(log["net"].iloc[-1])
    reasons, verdict = [], "GO"
    if proof_gaps:
        verdict = "FIX-PROOF"
        reasons.append("previous sessions have incomplete Proof")
    if dd_now >= cfg.drawdown_stop_usd:
        verdict = "STOP"
        reasons.append(f"drawdown ${dd_now:.2f} >= stop level ${cfg.drawdown_stop_usd:g}: v1 trading ends; "
                       "back to research (playbook §2.2)")
    elif dd_now >= cfg.drawdown_pause_usd:
        verdict = "PAUSE" if verdict == "GO" else verdict
        reasons.append(f"drawdown ${dd_now:.2f} >= pause level ${cfg.drawdown_pause_usd:g}: review before "
                       "the next session (playbook §2.2)")
    if sim_only_left and mode.upper() == "LIVE":
        verdict = "PAUSE" if verdict == "GO" else verdict
        reasons.append(f"rule violation recently: {sim_only_left} more SIM-only session(s)")
    if not reasons:
        reasons.append("all escalation checks clear")
    return {
        "mode": mode, "sessions": int(len(log)), "trades": int(len(trades)),
        "equity_usd": round(equity, 2), "drawdown_usd": round(dd_now, 2),
        "current_losing_streak": losing_streak, "current_no_trade_streak": no_trade_streak,
        "last_session_net_usd": round(last_day_net, 2),
        "sim_only_sessions_remaining": sim_only_left,
        "proof_gaps": proof_gaps, "verdict": verdict, "reasons": reasons,
    }


# --------------------------------------------------------------------------- reconcile
COMPARE_PRICES = ["entry_limit_price", "entry_price", "stop_price", "target_price", "exit_price"]


def reconcile(model: dict, logged: pd.Series, cfg: ob.Config) -> dict:
    tick = cfg.tick_size
    m_traded, l_traded = bool(model["traded"]), _bool(logged.get("traded"))
    rows, notes = [], []
    for col in ["traded", "no_trade_reason", "direction", "exit_reason"] + COMPARE_PRICES + ["net_usd"]:
        mv = model.get(col, np.nan)
        lv = logged.get(col, np.nan)
        if col == "traded":
            lv = l_traded
        rows.append({"field": col, "backtest_model": mv, "your_log": lv})

    if m_traded != l_traded or (m_traded and model.get("direction") != logged.get("direction")):
        verdict = "DECISION_MISMATCH"
        if not m_traded and model.get("no_trade_reason") == "entry_limit_not_filled":
            notes.append("Model: the next bar opened beyond the limit, so no fill. Paper platforms often fill "
                         "limits on a touch; if so this is a FILL-MODEL gap, not a rule break.")
        else:
            notes.append("Different trade decision. Check in this order: (1) did you follow the playbook "
                         "(rule_violation?), (2) do your platform bars match this data, (3) is the rule "
                         "ambiguous? Only (3) justifies a playbook patch.")
    elif not m_traded:
        same = str(model.get("no_trade_reason")) == str(logged.get("no_trade_reason"))
        verdict = "MATCH" if same else "REASON_MISMATCH"
        if not same:
            notes.append("Both stood aside but for different reasons: usually a logging slip; fix the log.")
    else:
        diffs = {}
        for col in COMPARE_PRICES:
            mv, lv = model.get(col), pd.to_numeric(logged.get(col), errors="coerce")
            if mv is not None and not pd.isna(mv) and not pd.isna(lv):
                diffs[col] = round((float(lv) - float(mv)) / tick, 1)
        if str(model.get("exit_reason")) != str(logged.get("exit_reason")):
            verdict = "OUTCOME_MISMATCH"
            notes.append("Same entry decision, different exit. Check stop/target prices and whether the "
                         "platform filled the target on a touch (model needs 1 tick through).")
        elif any(abs(v) > 1 for v in diffs.values()):
            verdict = "FILL_DIFF"
            notes.append("Same decisions, prices differ by more than 1 tick: track it as slippage evidence "
                         "(playbook §6 item 3).")
        else:
            verdict = "MATCH"
        notes.append(f"Price differences (your log minus model, in ticks; + = higher price): {diffs}")
    model_net = model.get("net_usd") if m_traded else 0.0
    return {"verdict": verdict, "model_net_usd": model_net, "table": pd.DataFrame(rows), "notes": notes}


def model_for_date(bars_path, data_tz, bar_label, cfg: ob.Config, date: str) -> dict:
    bars, bar_min, _ = ob.load_bars(bars_path, data_tz, bar_label, cfg.tick_size)
    day = bars[bars["date"] == pd.Timestamp(date).date()].reset_index(drop=True)
    if day.empty:
        raise ob.DataError(f"no regular-session bars for {date} in {bars_path}")
    return ob.run_backtest(day, cfg, bar_min, include_missing_weekdays=False).iloc[0].to_dict()


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rules")
    st = sub.add_parser("status")
    st.add_argument("--log", required=True)
    st.add_argument("--mode", default="SIM", choices=["SIM", "LIVE"])
    rc = sub.add_parser("reconcile")
    rc.add_argument("--log", required=True)
    rc.add_argument("--bars", required=True)
    rc.add_argument("--date", required=True)
    rc.add_argument("--data-tz", default=None)
    rc.add_argument("--bar-label", choices=["start", "end"], default="start")
    a = ap.parse_args(argv)
    cfg = ob.load_config(a.config)

    if a.cmd == "rules":
        for k, v in rules(cfg).items():
            print(f"{k:22s} {v}")
        return 0
    log = pd.read_csv(a.log, dtype=str)
    if a.cmd == "status":
        s = status(log, cfg, a.mode)
        for k, v in s.items():
            print(f"{k:28s} {v}")
        return 0 if s["verdict"] == "GO" else 1
    row = log[log["date"] == a.date]
    if row.empty:
        sys.exit(f"No row for {a.date} in {a.log}")
    try:
        model = model_for_date(a.bars, a.data_tz, a.bar_label, cfg, a.date)
    except ob.DataError as e:
        sys.exit(f"Data error: {e}")
    r = reconcile(model, row.iloc[-1], cfg)
    print(r["table"].to_string(index=False))
    print(f"\nVERDICT: {r['verdict']}   (model net after costs: {r['model_net_usd']})")
    for n in r["notes"]:
        print(f"- {n}")
    return 0 if r["verdict"] == "MATCH" else 1


if __name__ == "__main__":
    sys.exit(main())
