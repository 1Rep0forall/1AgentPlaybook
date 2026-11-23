#!/usr/bin/env python3
"""
Session tools for the daily One-Agent loop (see CLAUDE.md). Three commands:

  rules      Print today's rule numbers, DERIVED from the config (never hand-typed):
             max opening-range width, limit-order offset, target multiple.
                 python backtest/session_tools.py rules

  status     Read the paper/live trade log and say GO / PAUSE / STOP / FIX-PROOF,
             applying the playbook §2.2 escalation rules mechanically.
                 python backtest/session_tools.py status --log logs/trade_log_2026.csv

  log        Append one validated session row to the paper trade log. Stop, limit, target,
             risk, net $ and R are DERIVED from the config (same maths as the backtest);
             rule breaks are flagged and set rule_violation = Y automatically.
                 python backtest/session_tools.py log --log logs/trade_log_2026.csv --date 2026-11-23 \
                     --or-high 5012.25 --or-low 5009.5 --no-trade or_too_wide_for_risk_cap
                 python backtest/session_tools.py log --log logs/trade_log_2026.csv --date 2026-11-24 \
                     --or-high 5012.25 --or-low 5010 --direction long --signal-close 09:55 \
                     --entry-time 09:55 --entry-price 5012.75 --exit-time 10:40 --exit-price 5016.25 \
                     --exit-reason target --chart logs/charts/2026-11-24.png

  reconcile  Re-run the backtest engine on one session's 1-minute bars and compare
             it with what you logged. This is the core Proof step: it separates
             "I broke a rule" from "the fill model differs" from "the rule is ambiguous".
                 python backtest/session_tools.py reconcile --log logs/trade_log_2026.csv \
                     --bars data/session_2026-11-23.csv --data-tz America/Chicago --date 2026-11-23

All three read toolbox/backtest_config.json by default.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

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


# --------------------------------------------------------------------------- log
TEMPLATE = Path(__file__).resolve().parents[1] / "toolbox" / "trade_log_template.csv"
PLAYBOOK_VERSION = "v1.1"
LOGGABLE_REASONS = {k: v for k, v in {**ob.REASON_CODES, **ob.LIVE_ONLY_REASON_CODES}.items()
                    if k != "contract_roll_in_session"}           # a backtest-only data code
NO_RANGE_REASONS = {"no_session_data", "not_ready", "daily_loss_limit", "incomplete_or_data"}
EXIT_REASONS = ("target", "stop", "stop_gap", "time_exit")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class LogError(ValueError):
    pass


def template_columns() -> list:
    return TEMPLATE.read_text().splitlines()[0].split(",")


def _price(name, v, tick) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise LogError(f"{name} must be a number")
    if x <= 0 or abs(x / tick - round(x / tick)) > 1e-9:
        raise LogError(f"{name} {v} is not a price on the {tick} tick grid")
    return x


def _time(name, v) -> int:
    if not isinstance(v, str) or not TIME_RE.match(v.strip()):
        raise LogError(f"{name} must be HH:MM (24-hour, ET)")
    return ob.hhmm_to_min(v.strip())


def build_log_row(inp: dict, cfg: ob.Config, log: Optional[pd.DataFrame] = None) -> dict:
    """Validate one session's facts and derive every rule number from the config.
    Returns {"row": {...template columns...}, "violations": [...], "checks": [...]}.
    Violations force rule_violation = Y (playbook §2.3); checks are things to look at."""
    tick, pv = cfg.tick_size, cfg.point_value * cfg.contracts
    n = ob.derived_numbers(cfg)
    rt = n["round_trip_commission_usd"]
    violations, checks = [], []

    d_str = str(inp.get("date", "")).strip()
    try:
        day = date.fromisoformat(d_str)
    except ValueError:
        raise LogError("date must be YYYY-MM-DD")
    if day > date.today():
        raise LogError("date is in the future")
    if day.weekday() >= 5:
        checks.append("date is a weekend: is that right?")
    mode = str(inp.get("mode", "SIM")).upper()
    if mode != "SIM":
        raise LogError("Only SIM (paper) sessions can be logged: live trading is locked until playbook §6.")
    if log is not None and len(log) and ((log["date"] == d_str) & (log["mode"].str.upper() == mode)).any():
        raise LogError(f"{d_str} is already in the log: one row per session (edit the CSV to correct it)")

    row = {c: "" for c in template_columns()}
    row.update(date=d_str, playbook_version=PLAYBOOK_VERSION, mode=mode, contracts=str(cfg.contracts))
    traded = str(inp.get("traded", "")).lower() in ("true", "1", "yes", "y")
    reason = str(inp.get("no_trade_reason", "") or "").strip()

    has_range = not (not traded and reason in NO_RANGE_REASONS)
    if has_range:
        hi = _price("OR high", inp.get("or_high"), tick)
        lo = _price("OR low", inp.get("or_low"), tick)
        if hi <= lo:
            raise LogError("OR high must be above OR low")
        width = hi - lo
        min_risk = (width + n["precheck_extra_pts"]) * pv + rt
        row.update(or_high=f"{hi:g}", or_low=f"{lo:g}", or_width_pts=f"{width:g}", min_risk_usd=f"{min_risk:.2f}")
        precheck = ("or_too_narrow" if width < cfg.min_or_points else
                    "or_too_wide_for_risk_cap" if min_risk > cfg.max_risk_usd + 1e-9 else None)

    if not traded:
        if reason not in LOGGABLE_REASONS:
            raise LogError("pick a no-trade reason code from playbook §2.2")
        if has_range and precheck and reason != precheck:
            checks.append(f"the 09:45 pre-check gives '{precheck}' for this range, but '{reason}' was logged")
        row.update(traded="False", no_trade_reason=reason, net_usd="0", daily_pnl_usd="0")
    else:
        direction = str(inp.get("direction", "")).lower()
        if direction not in ("long", "short"):
            raise LogError("direction must be long or short")
        dsign = 1 if direction == "long" else -1
        sig = _time("signal bar close", inp.get("signal_bar_close"))
        t_in = _time("entry time", inp.get("entry_time"))
        t_out = _time("exit time", inp.get("exit_time"))
        entry = _price("entry price", inp.get("entry_price"), tick)
        exit_px = _price("exit price", inp.get("exit_price"), tick)
        exit_reason = str(inp.get("exit_reason", ""))
        if exit_reason not in EXIT_REASONS:
            raise LogError(f"exit reason must be one of {EXIT_REASONS}")
        stop = lo if dsign == 1 else hi
        limit = ob.risk_capped_limit(stop, dsign, cfg)
        risk_pts = (entry - stop) * dsign
        if risk_pts <= 0:
            raise LogError("entry price is on the wrong side of the stop for this direction")
        risk_usd = (risk_pts + cfg.stop_slippage_ticks * tick) * pv + rt
        raw_t = entry + dsign * cfg.target_r * risk_pts
        target = (np.ceil(raw_t / tick - 1e-9) if dsign == 1 else np.floor(raw_t / tick + 1e-9)) * tick
        gross = (exit_px - entry) * dsign * pv
        net = gross - rt
        es, ee, xt = (ob.hhmm_to_min(x) for x in (cfg.entry_start, cfg.entry_end, cfg.exit_time))

        if precheck:
            violations.append(f"traded although the 09:45 pre-check said '{precheck}'")
        if not (es < sig <= ee and (sig - es) % cfg.signal_minutes == 0):
            violations.append(f"signal bar close {inp['signal_bar_close']} is not a {cfg.signal_minutes}-minute bar "
                              f"close inside {cfg.entry_start}-{cfg.entry_end}")
        if not sig <= t_in <= sig + 1:
            violations.append("entry was not within the 1-minute bar after the signal (playbook §2.1 step 4)")
        if (entry - limit) * dsign > 1e-9:
            violations.append(f"filled at {entry:g}, beyond the risk-capped limit {limit:g}")
        if t_out < t_in:
            raise LogError("exit time is before entry time")
        if t_out > xt:
            violations.append(f"position held past {cfg.exit_time} ET")
        if exit_reason == "target" and abs(exit_px - target) > tick + 1e-9:
            checks.append(f"exit reason is target, but the exit {exit_px:g} is not at the target {target:g}")
        if exit_reason == "stop" and abs(exit_px - stop) > 2 * tick + 1e-9:
            checks.append(f"exit reason is stop, but the exit {exit_px:g} is far from the stop {stop:g}")
        if net < -cfg.daily_loss_limit_usd:
            checks.append(f"loss ${-net:.2f} exceeds the ${cfg.daily_loss_limit_usd:g} daily limit: investigate the fill")
        if not str(inp.get("chart_screenshot_path", "")).strip():
            checks.append("no chart screenshot path: status will report FIX-PROOF until it is added")
        row.update(traded="True", direction=direction, signal_bar_close=inp["signal_bar_close"].strip(),
                   entry_limit_price=f"{limit:g}", entry_time=inp["entry_time"].strip(), entry_price=f"{entry:g}",
                   stop_price=f"{stop:g}", target_price=f"{target:g}", risk_pts=f"{risk_pts:g}",
                   risk_usd=f"{risk_usd:.2f}", exit_time=inp["exit_time"].strip(), exit_price=f"{exit_px:g}",
                   exit_reason=exit_reason, gross_usd=f"{gross:.2f}", commission_usd=f"{rt:.2f}",
                   net_usd=f"{net:.2f}", r_multiple=f"{net / risk_usd:.3f}", daily_pnl_usd=f"{net:.2f}")

    prior = 0.0
    if log is not None and len(log):
        earlier = log[(log["mode"].str.upper() == mode) & (log["date"] < d_str)]
        prior = pd.to_numeric(earlier["net_usd"], errors="coerce").fillna(0).sum()
    row["equity_usd"] = f"{cfg.starting_capital + prior + float(row['net_usd'] or 0):.2f}"
    rv = str(inp.get("rule_violation", "N")).strip().upper()[:1] or "N"
    row["rule_violation"] = "Y" if violations else ("Y" if rv == "Y" else "N")
    for k in ("chart_screenshot_path", "event_tags", "broker_order_ids"):
        row[k] = str(inp.get(k, "") or "").strip()
    notes = [str(inp.get("notes", "") or "").strip()] + [f"auto-flag: {v}" for v in violations]
    row["notes"] = " | ".join(x for x in notes if x)
    for k, v in row.items():
        if any(ch in str(v) for ch in "\r\n"):
            raise LogError(f"{k} must be a single line")
    return {"row": row, "violations": violations, "checks": checks}


def append_log_row(path, row: dict) -> None:
    path = Path(path)
    cols = template_columns()
    new = not path.exists()
    if not new:
        header = path.read_text().splitlines()[0].split(",")
        if header != cols:
            raise LogError(f"{path} has different columns from toolbox/trade_log_template.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerow(row)


def read_log(path) -> Optional[pd.DataFrame]:
    p = Path(path)
    return pd.read_csv(p, dtype=str, keep_default_na=False) if p.exists() else None


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
    lg = sub.add_parser("log", help="append one validated session row")
    lg.add_argument("--log", required=True)
    lg.add_argument("--date", required=True)
    lg.add_argument("--or-high")
    lg.add_argument("--or-low")
    grp = lg.add_mutually_exclusive_group(required=True)
    grp.add_argument("--no-trade", metavar="REASON", help="a playbook §2.2 reason code")
    grp.add_argument("--direction", choices=["long", "short"])
    for flag in ("--signal-close", "--entry-time", "--entry-price", "--exit-time", "--exit-price",
                 "--chart", "--notes", "--event-tags", "--broker-ids"):
        lg.add_argument(flag, default="")
    lg.add_argument("--exit-reason", choices=EXIT_REASONS)
    lg.add_argument("--rule-violation", choices=["Y", "N"], default="N")
    lg.add_argument("--dry-run", action="store_true", help="validate and show the row without saving")
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
    if a.cmd == "log":
        inp = {"date": a.date, "or_high": a.or_high, "or_low": a.or_low, "traded": a.no_trade is None,
               "no_trade_reason": a.no_trade or "", "direction": a.direction or "",
               "signal_bar_close": a.signal_close, "entry_time": a.entry_time, "entry_price": a.entry_price,
               "exit_time": a.exit_time, "exit_price": a.exit_price, "exit_reason": a.exit_reason or "",
               "rule_violation": a.rule_violation, "chart_screenshot_path": a.chart, "notes": a.notes,
               "event_tags": a.event_tags, "broker_order_ids": a.broker_ids}
        try:
            res = build_log_row(inp, cfg, read_log(a.log))
            if not a.dry_run:
                append_log_row(a.log, res["row"])
        except LogError as e:
            sys.exit(f"Not logged: {e}")
        print(json.dumps({k: v for k, v in res["row"].items() if v != ""}, indent=2))
        for v in res["violations"]:
            print(f"RULE VIOLATION (rule_violation set to Y): {v}")
        for c in res["checks"]:
            print(f"CHECK: {c}")
        print("(dry run: nothing saved)" if a.dry_run else f"Saved to {a.log}")
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
