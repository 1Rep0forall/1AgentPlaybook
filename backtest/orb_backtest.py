#!/usr/bin/env python3
"""
MES Opening Range Breakout (ORB) — backtest starter script (v1.1)
=================================================================

WHAT IT DOES
------------
For each regular trading session (RTH, Eastern Time):
  1. Builds the opening range (OR) from the bars starting 09:30 up to 09:45 ET
     (all 15 one-minute bars required by default).
  2. Stands aside if the OR is too narrow, or too wide for the per-trade
     dollar risk cap even in the best case (a breakout close one tick beyond
     the range). The no-trade reason is logged for EVERY weekday, including
     weekdays with no data (holidays / gaps).
  3. Looks for the FIRST completed signal bar (default 5-min) that CLOSES
     outside the OR between 09:45 and 11:00 ET. Max 1 trade per session.
  4. Entry: a limit order at the worst price that still keeps all-in risk
     <= the cap. Fills at the next bar's open + slippage (never worse than
     the limit). If that open is already past the limit: no trade
     (`entry_limit_not_filled`).
  5. Stop at the opposite OR boundary, target at a fixed R-multiple (tick-
     rounded away from entry), flat at a fixed time-exit cutoff.
  6. Charges commissions + slippage, resolves ambiguous bars conservatively
     (stop first), fills gaps through the stop at the (worse) bar open.

Outputs (in <out>/<run_id>/): trade_log.csv, day_log.csv (every weekday),
equity_curve.csv, by_year.csv, by_regime.csv, sensitivity.csv, summary.json
and run_manifest.json (config, CLI args, data hash, script hash, git commit).

INPUT DATA FORMAT  (read this before running)
-----------------
CSV with a header row. Columns (case-insensitive):
    timestamp,open,high,low,close        required
    symbol                               optional but RECOMMENDED (e.g. ESH5);
                                         enables roll checks
    volume                               optional, ignored

  * timestamp : ISO-8601 text, e.g. "2019-05-06 09:30:00" or
                "2019-05-06T09:30:00-04:00" or "2019-05-06T13:30:00Z".
      - With a UTC offset / "Z": converted to America/New_York.
      - Naive (no offset): you MUST pass --data-tz, e.g. America/Chicago
        (typical CME vendor data), UTC, or America/New_York.
      - A file mixing both styles is rejected.
  * Bar labelling : by default a timestamp marks the bar START
                ("09:30" = 09:30:00-09:30:59). If your vendor stamps the bar
                END, pass --bar-label end.
  * Bar size  : 1-minute bars strongly recommended (fewer ambiguous bars).
                Any bar size that divides 15 minutes works (1, 3, 5, 15) and
                must also divide `signal_minutes`.
  * Prices    : a continuous FRONT-MONTH series, either UNADJUSTED (rolled
                on a volume/calendar rule) or ADDITIVE (difference) back-
                adjusted, on the 0.25 tick grid. Ratio-adjusted data (off-grid
                prices) is rejected: it distorts point ranges and $ risk.
  * Include ~10 minutes of pre-open bars (09:20-09:29 ET) if you can: they
    let the script verify that 09:30 really is the cash open in every month
    (catches timezone / daylight-saving mistakes).
  * Include >= 13 months of history before your test start_date so the
    regime labels (trailing 252-day volatility rank, 200-day SMA) are ready.
  * MES only exists since May 2019. For a 10-year test use ES 1-min data as a
    proxy: point moves are identical, the script applies MES's $5/point.

USAGE
-----
    python backtest/orb_backtest.py --period dev --data data/es_1min.csv \
        --data-tz America/Chicago --config toolbox/backtest_config.json

  --period is REQUIRED:
    dev      only sessions in [dev_start, dev_end]; run as often as needed.
    holdout  only sessions >= holdout_start. ONE use ever (Blueprint §2.3);
             refused unless the git worktree is clean, so the result is tied
             to a committed config + code.

Requires: Python 3.9+, pandas, numpy. matplotlib optional (equity chart).
Tests:    python -m pytest tests/

HONESTY NOTES
-------------
  * A backtest is a hypothesis test on the past, not a forecast.
  * Bar data cannot tell you the true intra-bar path; ambiguous bars are
    resolved conservatively by default and reported so you can see how much
    the result depends on that assumption.
  * Change rules ONLY by creating a new playbook version and a new run id.
    Do not iterate parameters on the full 10 years (curve-fitting).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

SCRIPT_VERSION = "1.1.0"
ET = "America/New_York"
RTH_START_MIN = 9 * 60 + 30   # 09:30 ET
RTH_END_MIN = 16 * 60         # 16:00 ET
NEVER = 10**9                 # sentinel index: "event never happened"

# Data-hygiene / reporting constants. Deliberately NOT in the config file:
# the config holds trading rules and the cost model only, so every key in it
# is something the playbook tells you to do.
MIN_OR_COVERAGE = 1.0          # all opening-range bars must exist
MAX_OFF_GRID_FRACTION = 0.01   # above this share of off-tick rows the file is rejected
MONTE_CARLO_RUNS = 5000
MIN_TRADES_FOR_MONTE_CARLO = 30
RANDOM_SEED = 42

# Every no-trade reason the backtest can emit. The playbook's no-trade table
# uses the same codes (enforced by tests/test_orb_backtest.py).
REASON_CODES: Dict[str, str] = {
    "no_session_data": "Weekday with no bars (exchange holiday or missing data)",
    "contract_roll_in_session": "More than one contract symbol inside the session",
    "incomplete_or_data": "Opening-range bars missing",
    "or_too_narrow": "OR width below min_or_points",
    "or_too_wide_for_risk_cap": "Even a 1-tick breakout entry would exceed the $ risk cap",
    "no_data_in_entry_window": "No bars between entry_start and entry_end",
    "incomplete_signal_data": "A signal bar was missing data before any breakout was seen",
    "no_breakout": "No signal bar closed outside the OR by entry_end",
    "no_entry_bar": "No bar available to enter on after the signal",
    "entry_beyond_stop": "Next bar opened back through the stop level",
    "entry_limit_not_filled": "Next bar opened past the risk-capped limit price",
}

# Codes only a person can log (paper/live), never emitted by the backtest.
LIVE_ONLY_REASON_CODES: Dict[str, str] = {
    "daily_loss_limit": "Day P&L <= -daily_loss_limit_usd: no more trading today",
    "not_ready": "Pre-session checklist not fully green (incl. status verdict not GO)",
}

# Columns shared by the backtest trade log and the live/paper trade log
# template (enforced by tests so the two stay comparable).
SHARED_LOG_COLUMNS = [
    "date", "traded", "no_trade_reason", "or_high", "or_low", "or_width_pts",
    "min_risk_usd", "direction", "signal_bar_close", "entry_limit_price",
    "entry_time", "entry_price", "stop_price", "target_price", "risk_pts",
    "risk_usd", "exit_time", "exit_price", "exit_reason", "gross_usd",
    "commission_usd", "slippage_usd", "net_usd", "r_multiple",
]


# --------------------------------------------------------------------------
# Configuration — every rule is fixed here BEFORE the test is run.
# --------------------------------------------------------------------------
@dataclass
class Config:
    # Instrument (MES)
    point_value: float = 5.0          # $ per index point per contract
    tick_size: float = 0.25           # 1 tick = $1.25 on MES
    contracts: int = 1
    starting_capital: float = 500.0

    # Session windows (ET, "HH:MM")
    or_start: str = "09:30"
    or_end: str = "09:45"
    entry_start: str = "09:45"
    entry_end: str = "11:00"          # signal bar must CLOSE by this time
    exit_time: str = "12:00"          # flat at the open of the first bar >= this
    signal_minutes: int = 5           # breakout is judged on completed N-min bars

    # Entry / exits / risk
    target_r: float = 1.5             # the one pre-registered target
    max_risk_usd: float = 25.0        # all-in $ risk cap per trade (stop + slip + fees)
    daily_loss_limit_usd: float = 50.0
    min_or_points: float = 1.0        # skip degenerate/tiny ranges

    # Account escalation (playbook §2.2) — reported by the backtest, enforced
    # in paper/live by backtest/session_tools.py status
    drawdown_pause_usd: float = 100.0
    drawdown_stop_usd: float = 150.0

    # Costs
    commission_per_side_usd: float = 1.00   # commission + exchange + NFA, per contract per side
    entry_slippage_ticks: int = 1
    stop_slippage_ticks: int = 1
    time_exit_slippage_ticks: int = 1
    target_slippage_ticks: int = 0          # limit order (no slippage)
    target_trade_through_ticks: int = 1     # limit counts as filled only if price trades THROUGH it

    # Ambiguity: when stop and target are both inside one bar
    ambiguous_bar_rule: str = "stop_first"  # "stop_first" (conservative) | "target_first"

    # Research periods (Blueprint §2.3). The hold-out is used ONCE.
    dev_start: str = "2015-01-01"
    dev_end: str = "2021-12-31"
    holdout_start: str = "2022-01-01"
    # Optional narrowing INSIDE the chosen period (e.g. MES-only from 2019-05-06)
    start_date: Optional[str] = None
    end_date: Optional[str] = None


def hhmm_to_min(s: str) -> int:
    hh, mm = s.split(":")
    return int(hh) * 60 + int(mm)


def min_to_hhmm(m: Optional[int]) -> str:
    return "" if m is None else f"{m // 60:02d}:{m % 60:02d}"


def validate_config(cfg: Config) -> None:
    def fail(msg):
        raise ValueError(msg)
    if cfg.ambiguous_bar_rule not in ("stop_first", "target_first"):
        fail("ambiguous_bar_rule must be 'stop_first' or 'target_first'")
    if not pd.Timestamp(cfg.dev_start) <= pd.Timestamp(cfg.dev_end) < pd.Timestamp(cfg.holdout_start):
        fail("periods must satisfy dev_start <= dev_end < holdout_start")
    if not 0 < cfg.drawdown_pause_usd <= cfg.drawdown_stop_usd:
        fail("need 0 < drawdown_pause_usd <= drawdown_stop_usd")
    ors, ore = hhmm_to_min(cfg.or_start), hhmm_to_min(cfg.or_end)
    es, ee, xt = hhmm_to_min(cfg.entry_start), hhmm_to_min(cfg.entry_end), hhmm_to_min(cfg.exit_time)
    if not (RTH_START_MIN <= ors < ore <= es < ee < xt <= RTH_END_MIN):
        fail("windows must satisfy 09:30 <= or_start < or_end <= entry_start < entry_end < exit_time <= 16:00")
    if (ee - es) % cfg.signal_minutes != 0:
        fail("entry window length must be a multiple of signal_minutes")
    if derived_numbers(cfg)["limit_offset_pts"] <= 0:
        fail("max_risk_usd is too small to cover fees and slippage")


def derived_numbers(cfg: Config) -> dict:
    """The session numbers the playbook/checklist quote, derived from the
    config so they can never drift from it (tests compare them to the docs)."""
    tick, pv = cfg.tick_size, cfg.point_value * cfg.contracts
    rt_comm = 2 * cfg.commission_per_side_usd * cfg.contracts
    allowed_pts = (cfg.max_risk_usd - rt_comm) / pv - cfg.stop_slippage_ticks * tick
    limit_ticks = int(np.floor(allowed_pts / tick + 1e-9))
    # Pre-check passes while (width + (1 + entry_slip + stop_slip) ticks) * pv + fees <= cap
    width_ticks = int(np.floor(((cfg.max_risk_usd - rt_comm) / pv) / tick + 1e-9)) \
        - (1 + cfg.entry_slippage_ticks + cfg.stop_slippage_ticks)
    return {
        "round_trip_commission_usd": rt_comm,
        "limit_offset_pts": limit_ticks * tick,          # limit = stop +/- this
        "max_or_width_pts": width_ticks * tick,          # widest range the 09:45 pre-check accepts
        "precheck_extra_pts": (1 + cfg.entry_slippage_ticks + cfg.stop_slippage_ticks) * tick,
        "min_or_points": cfg.min_or_points,
        "target_r": cfg.target_r,
    }


def load_config(path: Optional[str]) -> Config:
    cfg = Config()
    if path:
        raw = json.loads(Path(path).read_text())
        valid = {f.name for f in fields(Config)}
        unknown = [k for k in raw if not k.startswith("_") and k not in valid]
        if unknown:  # catches typos that would otherwise silently use defaults
            sys.exit(f"Unknown config keys: {unknown}")
        cfg = replace(cfg, **{k: v for k, v in raw.items() if k in valid})
    try:
        validate_config(cfg)
    except ValueError as e:
        sys.exit(f"Config error: {e}")
    return cfg


# --------------------------------------------------------------------------
# Data loading & hygiene
# --------------------------------------------------------------------------
class DataError(ValueError):
    pass


def parse_timestamps(raw: pd.Series, data_tz: Optional[str]) -> pd.Series:
    raw = raw.astype(str).str.strip()
    offset_share = raw.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True).mean()
    if 0 < offset_share < 1:
        raise DataError(f"{offset_share:.1%} of timestamps carry a UTC offset and the rest do not; "
                        "fix the file so all rows use one style.")
    if offset_share == 1:
        return pd.to_datetime(raw, utc=True, format="ISO8601").dt.tz_convert(ET)
    if not data_tz:
        raise DataError("Timestamps have no UTC offset: pass --data-tz (e.g. America/Chicago).")
    ts = pd.to_datetime(raw, format="ISO8601")
    # Ambiguous/nonexistent local times only occur around 01:00-03:00 (overnight),
    # never in RTH; they become NaT and are dropped and counted.
    return ts.dt.tz_localize(data_tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert(ET)


def open_alignment_check(df: pd.DataFrame) -> dict:
    """Heuristic timezone/DST check: the 09:30-09:34 ET bars should be much more
    active (larger bar ranges) than a reference window, in EVERY month. A month
    that fails suggests the timestamps are shifted (e.g. a vendor export that
    ignores daylight saving). Uses 09:20-09:29 pre-open bars when present,
    otherwise 10:30-10:59."""
    rng = (df["high"] - df["low"]).to_numpy()
    tod = df["tod"].to_numpy()
    month = pd.Series(df["date"].astype(str).str[:7].to_numpy())
    open_m = (tod >= 570) & (tod < 575)
    pre_m = (tod >= 560) & (tod < 570)
    sessions = df["date"].nunique()
    pre_sessions = df.loc[pre_m, "date"].nunique()
    use_pre = sessions > 0 and pre_sessions / sessions > 0.5
    ref_m = pre_m if use_pre else (tod >= 630) & (tod < 660)
    frame = pd.DataFrame({"month": month, "rng": rng, "open": open_m, "ref": ref_m})
    o = frame[frame["open"]].groupby("month")["rng"].mean()
    r = frame[frame["ref"]].groupby("month")["rng"].mean()
    ratio = (o / r.replace(0, np.nan)).dropna()
    threshold = 1.5
    flagged = ratio[ratio < threshold]
    return {
        "reference_window": "09:20-09:29" if use_pre else "10:30-10:59",
        "threshold_ratio": threshold,
        "months_checked": int(ratio.size),
        "months_flagged": int(flagged.size),
        "flagged": {k: round(float(v), 2) for k, v in flagged.items()},
        "median_ratio": round(float(ratio.median()), 2) if ratio.size else None,
    }


def load_bars(path: str, data_tz: Optional[str], bar_label: str, tick: float = 0.25,
              max_off_grid_fraction: float = MAX_OFF_GRID_FRACTION):
    """Returns (rth_bars, bar_minutes, hygiene_report). Raises DataError."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in ("timestamp", "open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise DataError(f"Missing required columns: {missing}")

    out = pd.DataFrame({"ts": parse_timestamps(df["timestamp"], data_tz)})
    for c in ("open", "high", "low", "close"):
        out[c] = pd.to_numeric(df[c], errors="coerce")
    has_symbol = "symbol" in df.columns
    if has_symbol:
        out["symbol"] = df["symbol"].astype(str).str.strip()
    report = {"rows_in": int(len(out)), "symbol_column": has_symbol}

    out = out.dropna()
    report["rows_dropped_nan_or_bad_time"] = report["rows_in"] - int(len(out))
    out = out.sort_values("ts", kind="stable")
    px_cols = ["open", "high", "low", "close"]
    dup_all = out.duplicated("ts", keep=False)
    conflicting = out[dup_all].groupby("ts")[px_cols].nunique().gt(1).any(axis=1).sum()
    n = len(out)
    out = out.drop_duplicates("ts", keep="first")
    report["duplicate_timestamps_dropped"] = n - int(len(out))
    report["duplicate_timestamps_with_conflicting_prices"] = int(conflicting)

    bad = (out["high"] < out[["open", "close"]].max(axis=1)) | (out["low"] > out[["open", "close"]].min(axis=1))
    report["bad_ohlc_rows_dropped"] = int(bad.sum())
    out = out[~bad]

    # Tick grid: real exchange prices sit on the 0.25 grid.
    ticks = out[px_cols].to_numpy() / tick
    off = np.abs(ticks - np.round(ticks)) > 1e-6
    off_rows = int(off.any(axis=1).sum())
    report["off_tick_grid_rows"] = off_rows
    if len(out) and off_rows / len(out) > max_off_grid_fraction:
        raise DataError(f"{off_rows / len(out):.1%} of rows have prices off the {tick} tick grid — "
                        "ratio-adjusted or corrupted data? Use unadjusted or additive-adjusted prices.")
    out[px_cols] = np.round(ticks) * tick  # snap the few stragglers (float noise)

    # Infer bar size from the most common spacing between consecutive bars.
    diffs = out["ts"].diff().dt.total_seconds().div(60).dropna()
    bar_min = int(diffs[diffs > 0].mode().iloc[0])
    if bar_min < 1 or 15 % bar_min != 0:
        raise DataError(f"Inferred bar size {bar_min} min does not divide the 15-min opening range.")
    if bar_label == "end":
        out["ts"] = out["ts"] - pd.Timedelta(minutes=bar_min)
    report["bar_minutes"] = bar_min

    out["date"] = out["ts"].dt.date
    out["tod"] = (out["ts"].dt.hour * 60 + out["ts"].dt.minute).astype(int)
    report["open_alignment"] = open_alignment_check(out)
    out = out[(out["tod"] >= RTH_START_MIN) & (out["tod"] < RTH_END_MIN)].reset_index(drop=True)
    if out.empty:
        raise DataError("No regular-session (09:30-16:00 ET) bars found — check --data-tz / --bar-label.")

    med = float(out["close"].median())
    report["median_close_warning"] = (not 500 <= med <= 20000)
    if has_symbol:
        n_sym = out.groupby("date")["symbol"].nunique()
        roll_days = n_sym[n_sym > 1].index
        report["sessions_with_multiple_symbols"] = int(len(roll_days))
        report["multiple_symbol_dates_sample"] = [str(d) for d in list(roll_days)[:20]]
    report.update(rth_rows=int(len(out)), sessions=int(out["date"].nunique()),
                  first_bar=str(out["ts"].iloc[0]), last_bar=str(out["ts"].iloc[-1]))
    return out, bar_min, report


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Single-session simulation
# --------------------------------------------------------------------------
def first_true(mask: np.ndarray) -> int:
    """Index of first True, or NEVER if none."""
    idx = np.flatnonzero(mask)
    return int(idx[0]) if idx.size else NEVER


def risk_capped_limit(stop: float, d: int, cfg: Config) -> float:
    """Worst entry price at which all-in risk (stop distance + stop slippage +
    round-trip commission) still fits within max_risk_usd."""
    return stop + d * derived_numbers(cfg)["limit_offset_pts"]


def simulate_day(date, tod, o, h, l, c, cfg: Config, bar_min: int, target_r: float) -> dict:
    rec = {"date": date, "traded": False, "no_trade_reason": ""}
    tick = cfg.tick_size
    pv = cfg.point_value * cfg.contracts
    rt_comm = 2 * cfg.commission_per_side_usd * cfg.contracts
    ors, ore = hhmm_to_min(cfg.or_start), hhmm_to_min(cfg.or_end)
    es, ee = hhmm_to_min(cfg.entry_start), hhmm_to_min(cfg.entry_end)
    xt = hhmm_to_min(cfg.exit_time)

    def no_trade(reason, **extra):
        rec.update(extra, no_trade_reason=reason)
        return rec

    # 1) Opening range ----------------------------------------------------
    in_or = (tod >= ors) & (tod < ore)
    if in_or.sum() < MIN_OR_COVERAGE * (ore - ors) / bar_min - 1e-9:
        return no_trade("incomplete_or_data")
    or_hi, or_lo = float(h[in_or].max()), float(l[in_or].min())
    width = or_hi - or_lo
    rec.update(or_high=or_hi, or_low=or_lo, or_width_pts=width)
    if width < cfg.min_or_points:
        return no_trade("or_too_narrow")

    # Best-case risk: signal closes 1 tick beyond the range, next bar opens
    # there, plus entry slippage, plus stop slippage and round-trip fees.
    min_risk = (width + (1 + cfg.entry_slippage_ticks + cfg.stop_slippage_ticks) * tick) * pv + rt_comm
    rec["min_risk_usd"] = round(min_risk, 2)
    if min_risk > cfg.max_risk_usd + 1e-9:
        return no_trade("or_too_wide_for_risk_cap")

    # 2) Signal: first COMPLETE N-min bar closing outside the OR ------------
    win = np.flatnonzero((tod >= es) & (tod < ee))
    if win.size == 0:
        return no_trade("no_data_in_entry_window")
    bucket = (tod[win] - es) // cfg.signal_minutes
    bars_needed = cfg.signal_minutes // bar_min
    direction, signal_close_min = 0, None
    for b in range(0, (ee - es) // cfg.signal_minutes):
        members = win[bucket == b]
        close_min = es + (b + 1) * cfg.signal_minutes
        if members.size < bars_needed or tod[members[-1]] != close_min - bar_min:
            # We cannot know whether this bar was a breakout: stand aside.
            return no_trade("incomplete_signal_data")
        px = c[members[-1]]
        if px > or_hi:
            direction, signal_close_min = 1, close_min
            break
        if px < or_lo:
            direction, signal_close_min = -1, close_min
            break
    if direction == 0:
        return no_trade("no_breakout")
    d = direction
    stop = or_lo if d == 1 else or_hi
    limit = risk_capped_limit(stop, d, cfg)
    rec.update(direction="long" if d == 1 else "short", signal_bar_close=min_to_hhmm(signal_close_min),
               stop_price=stop, entry_limit_price=limit)

    # 3) Entry on the next bar's open -------------------------------------
    after = np.flatnonzero(tod >= signal_close_min)
    if after.size == 0 or tod[after[0]] > ee:
        return no_trade("no_entry_bar")
    k = int(after[0])
    open_k = float(o[k])
    if (open_k - stop) * d <= 0:
        return no_trade("entry_beyond_stop")
    if (open_k - limit) * d > 0:
        return no_trade("entry_limit_not_filled")
    entry = open_k + d * cfg.entry_slippage_ticks * tick
    if (entry - limit) * d > 0:          # a limit order never fills worse than its price
        entry = limit
    entry_slip_usd = (entry - open_k) * d * pv
    risk_pts = (entry - stop) * d
    risk_usd = (risk_pts + cfg.stop_slippage_ticks * tick) * pv + rt_comm

    def finish(exit_px, exit_min, reason, exit_slip_ticks, target, ambiguous):
        net = (exit_px - entry) * d * pv - rt_comm
        rec.update(
            traded=True, entry_time=min_to_hhmm(int(tod[k])), entry_price=entry,
            target_price=target, risk_pts=risk_pts, risk_usd=round(risk_usd, 2),
            exit_time=min_to_hhmm(exit_min), exit_price=exit_px, exit_reason=reason,
            ambiguous_bar=bool(ambiguous), pnl_pts=(exit_px - entry) * d,
            gross_usd=round((exit_px - entry) * d * pv, 2), commission_usd=rt_comm,
            slippage_usd=round(entry_slip_usd + exit_slip_ticks * tick * pv, 2),
            net_usd=round(net, 2), r_multiple=round(net / risk_usd, 3),
            daily_limit_breach=bool(net < -cfg.daily_loss_limit_usd),
        )
        return rec

    # Snap target to the tick grid, rounding AWAY from entry (conservative).
    raw_target = entry + d * target_r * risk_pts
    target = (np.ceil(raw_target / tick - 1e-9) if d == 1 else np.floor(raw_target / tick + 1e-9)) * tick

    # 4) Manage the trade bar by bar until the time exit -------------------
    k_exit = int(np.searchsorted(tod, xt, side="left"))  # first bar at/after exit time
    seg = slice(k, k_exit)
    oo, hh, ll = o[seg], h[seg], l[seg]
    thru = cfg.target_trade_through_ticks * tick
    if d == 1:
        si, ti = first_true(ll <= stop), first_true(hh >= target + thru)
    else:
        si, ti = first_true(hh >= stop), first_true(ll <= target - thru)

    ambiguous = si == ti and si < NEVER
    if si == ti == NEVER:  # neither hit: time exit
        if k_exit < len(tod):
            exit_raw, exit_min, reason = float(o[k_exit]), int(tod[k_exit]), "time_exit"
        else:  # early close / data ends before cutoff
            exit_raw, exit_min, reason = float(c[-1]), int(tod[-1]) + bar_min, "data_end_exit"
        exit_slip = cfg.time_exit_slippage_ticks
    elif si < ti or (ambiguous and cfg.ambiguous_bar_rule == "stop_first"):
        gapped = (oo[si] <= stop) if d == 1 else (oo[si] >= stop)
        gapped = bool(gapped) and si > 0  # the entry bar opens at our entry, beyond the stop
        exit_raw = float(oo[si]) if gapped else stop
        exit_min, reason = int(tod[k + si]), ("stop_gap" if gapped else "stop")
        exit_slip = cfg.stop_slippage_ticks
    else:
        exit_raw, exit_min, reason = target, int(tod[k + ti]), "target"
        exit_slip = cfg.target_slippage_ticks
    exit_px = exit_raw - d * exit_slip * tick
    return finish(exit_px, exit_min, reason, exit_slip, target, ambiguous)


TRADE_COLUMNS = [
    "or_high", "or_low", "or_width_pts", "min_risk_usd", "direction", "signal_bar_close",
    "entry_limit_price", "entry_time", "entry_price", "stop_price", "target_price", "risk_pts",
    "risk_usd", "exit_time", "exit_price", "exit_reason", "ambiguous_bar", "pnl_pts", "gross_usd",
    "commission_usd", "slippage_usd", "net_usd", "r_multiple", "daily_limit_breach",
]


def run_backtest(bars: pd.DataFrame, cfg: Config, bar_min: int, target_r: Optional[float] = None,
                 include_missing_weekdays: bool = True) -> pd.DataFrame:
    target_r = cfg.target_r if target_r is None else target_r
    dates = bars["date"].to_numpy()
    tod = bars["tod"].to_numpy()
    o, h, l, c = (bars[x].to_numpy(dtype=float) for x in ("open", "high", "low", "close"))
    multi_symbol = set()
    if "symbol" in bars.columns:
        n_sym = bars.groupby("date")["symbol"].nunique()
        multi_symbol = set(n_sym[n_sym > 1].index)
    _, starts = np.unique(dates, return_index=True)
    ends = np.r_[starts[1:], len(dates)]
    rows = []
    for s, e in zip(starts, ends):
        if dates[s] in multi_symbol:  # never build a range across two contracts
            rows.append({"date": dates[s], "traded": False, "no_trade_reason": "contract_roll_in_session"})
            continue
        # 1 trade max per session is enforced structurally: one call per day.
        rows.append(simulate_day(dates[s], tod[s:e], o[s:e], h[s:e], l[s:e], c[s:e], cfg, bar_min, target_r))
    if include_missing_weekdays and len(dates):
        have = set(dates[starts])
        for d in pd.bdate_range(dates[starts[0]], dates[starts[-1]]).date:
            if d not in have:
                rows.append({"date": d, "traded": False, "no_trade_reason": "no_session_data"})
    df = pd.DataFrame(rows)
    # Guarantee a stable schema even when zero trades occur (common with tight risk caps).
    for col in TRADE_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    df["traded"] = df["traded"].astype(bool)
    return df.sort_values("date", kind="stable").reset_index(drop=True)


# --------------------------------------------------------------------------
# Regimes (computed ONLY from information available before each session)
# --------------------------------------------------------------------------
def compute_regimes(bars: pd.DataFrame) -> pd.DataFrame:
    """Per-date regime labels. Call on the FULL history (before any date
    filter) so the 252/200-day lookbacks are warmed up."""
    agg = dict(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"))
    if "symbol" in bars.columns:
        agg.update(sym_first=("symbol", "first"), sym_last=("symbol", "last"))
    daily = bars.groupby("date").agg(**agg)
    prev_c = daily["c"].shift(1)
    gap_terms = pd.concat([(daily["h"] - prev_c).abs(), (daily["l"] - prev_c).abs()], axis=1).max(axis=1)
    if "sym_first" in daily.columns:
        # On a roll day the prior close belongs to another contract: drop the
        # (artificial) gap from the true range.
        rolled = daily["sym_first"] != daily["sym_last"].shift(1)
        gap_terms = gap_terms.where(~rolled, 0.0)
    tr = pd.concat([daily["h"] - daily["l"], gap_terms], axis=1).max(axis=1)
    atr_pct = (tr.rolling(20).mean() / daily["c"]).shift(1)        # prior 20 days, % of price
    vol_rank = atr_pct.rolling(252).rank(pct=True)                   # vs trailing year
    vol = pd.cut(vol_rank, [0, 1 / 3, 2 / 3, 1.0], labels=["low_vol", "mid_vol", "high_vol"],
                 include_lowest=True).astype(object).fillna("warmup")
    sma200 = daily["c"].rolling(200).mean().shift(1)
    trend = np.where(sma200.isna(), "warmup", np.where(prev_c > sma200, "uptrend", "downtrend"))
    return pd.DataFrame({"vol_regime": vol, "trend_regime": trend}, index=daily.index)


def add_regimes(day_log: pd.DataFrame, regimes: pd.DataFrame) -> pd.DataFrame:
    out = day_log.merge(regimes, left_on="date", right_index=True, how="left")
    out[["vol_regime", "trend_regime"]] = out[["vol_regime", "trend_regime"]].fillna("no_data")
    return out


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def longest_streak(mask: np.ndarray) -> int:
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best


def max_drawdown(pnl: np.ndarray, start: float = 0.0) -> float:
    if pnl.size == 0:
        return 0.0
    eq = start + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.r_[start, eq])[1:]
    return float((peak - eq).max())


def core_metrics(trades: pd.DataFrame, start: float = 0.0) -> dict:
    pnl = trades["net_usd"].to_numpy(dtype=float) if len(trades) else np.array([])
    n = pnl.size
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gl = -losses.sum()
    return {
        "trades": n,
        "win_rate": round(wins.size / n, 3) if n else None,
        "net_pnl_usd": round(float(pnl.sum()), 2),
        "profit_factor": round(float(wins.sum() / gl), 2) if gl > 0 else None,
        "expectancy_usd": round(float(pnl.mean()), 2) if n else None,
        "expectancy_r": round(float(trades["r_multiple"].mean()), 3) if n else None,
        "max_drawdown_usd": round(max_drawdown(pnl, start), 2),
        "longest_losing_streak": longest_streak(pnl <= 0),
    }


def with_data(day_log: pd.DataFrame) -> pd.DataFrame:
    return day_log[day_log["no_trade_reason"] != "no_session_data"]


def full_summary(day_log: pd.DataFrame, cfg: Config) -> dict:
    sessions = with_data(day_log)
    trades = day_log[day_log["traded"]]
    pnl = trades["net_usd"].to_numpy(dtype=float)
    s = {"sessions_with_data": int(len(sessions)),
         "weekdays_without_data": int(len(day_log) - len(sessions)),
         "trade_rate": round(len(trades) / max(len(sessions), 1), 3)}
    s.update(core_metrics(trades, cfg.starting_capital))
    if len(trades):
        eq = cfg.starting_capital + np.cumsum(pnl)
        peak = np.maximum.accumulate(np.r_[cfg.starting_capital, eq])[1:]
        dd = peak - eq
        pause, stop = np.flatnonzero(dd >= cfg.drawdown_pause_usd), np.flatnonzero(dd >= cfg.drawdown_stop_usd)
        s.update(
            avg_win_usd=round(float(pnl[pnl > 0].mean()), 2) if (pnl > 0).any() else None,
            avg_loss_usd=round(float(pnl[pnl <= 0].mean()), 2) if (pnl <= 0).any() else None,
            gross_pnl_before_costs_usd=round(float(trades["gross_usd"].sum() + trades["slippage_usd"].sum()), 2),
            total_commission_usd=round(float(trades["commission_usd"].sum()), 2),
            total_slippage_usd=round(float(trades["slippage_usd"].sum()), 2),
            max_drawdown_pct_of_start=round(100 * s["max_drawdown_usd"] / cfg.starting_capital, 1),
            ending_equity_usd=round(float(eq[-1]), 2),
            lowest_equity_usd=round(float(min(eq.min(), cfg.starting_capital)), 2),
            # Report-only: the backtest keeps trading so you see the full distribution;
            # in paper/live the playbook would have paused / stopped on these dates.
            first_date_dd_reached_pause=str(trades["date"].iloc[pause[0]]) if pause.size else None,
            first_date_dd_reached_stop=str(trades["date"].iloc[stop[0]]) if stop.size else None,
            exit_reasons={k: int(v) for k, v in trades["exit_reason"].value_counts().items()},
            ambiguous_bars=int(trades["ambiguous_bar"].astype(bool).sum()),
            ambiguous_bar_share=round(float(trades["ambiguous_bar"].astype(bool).mean()), 3),
            daily_loss_limit_breaches=int(trades["daily_limit_breach"].astype(bool).sum()),
            longest_sessions_without_a_win_incl_no_trade_days=longest_streak(
                ~(sessions["traded"] & (sessions["net_usd"].fillna(0) > 0)).to_numpy()),
        )
    s["no_trade_reasons"] = {k: int(v) for k, v in
                             day_log.loc[~day_log["traded"], "no_trade_reason"].value_counts().items()}
    return s


def grouped(day_log: pd.DataFrame, key: str) -> pd.DataFrame:
    rows = []
    for k, g in with_data(day_log).groupby(key, sort=True):
        t = g[g["traded"]]
        r = {key: k, "sessions": len(g), "no_trade_days": int((~g["traded"]).sum())}
        r.update(core_metrics(t, 0.0))  # drawdown measured from the start of each bucket
        rows.append(r)
    return pd.DataFrame(rows)


def monte_carlo(pnl: np.ndarray, cfg: Config) -> dict:
    """Bootstrap trade order (assumes trades are independent — they may not be)."""
    if pnl.size < MIN_TRADES_FOR_MONTE_CARLO:
        return {"note": f"only {pnl.size} trades (< {MIN_TRADES_FOR_MONTE_CARLO}); "
                        "Monte Carlo skipped — too few trades to say anything about path risk"}
    rng = np.random.default_rng(RANDOM_SEED)
    sims = rng.choice(pnl, size=(MONTE_CARLO_RUNS, pnl.size), replace=True)
    eq = np.cumsum(sims, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((sims.shape[0], 1)), eq], axis=1), axis=1)[:, 1:]
    mdd = (peak - eq).max(axis=1)
    loss = sims <= 0
    idx = np.arange(pnl.size)
    last_reset = np.maximum.accumulate(np.where(~loss, idx, -1), axis=1)
    streak = (idx - last_reset).max(axis=1)
    half = cfg.starting_capital / 2
    return {
        "runs": MONTE_CARLO_RUNS,
        "max_dd_usd_p50": round(float(np.percentile(mdd, 50)), 2),
        "max_dd_usd_p95": round(float(np.percentile(mdd, 95)), 2),
        "losing_streak_p50": int(np.percentile(streak, 50)),
        "losing_streak_p95": int(np.percentile(streak, 95)),
        "prob_dd_ge_half_account": round(float((mdd >= half).mean()), 3),
        "prob_net_loss": round(float((eq[:, -1] < 0).mean()), 3),
    }


def reprice_double_costs(trades: pd.DataFrame) -> pd.DataFrame:
    """Same trades, twice the commission and slippage (isolates the cost effect)."""
    t = trades.copy()
    t["net_usd"] = t["net_usd"] - t["commission_usd"] - t["slippage_usd"]
    t["r_multiple"] = t["net_usd"] / t["risk_usd"]
    return t


def sensitivity(bars, cfg: Config, bar_min: int, base_day_log: pd.DataFrame) -> pd.DataFrame:
    """Pre-declared robustness checks. Do NOT pick the best row and call it the strategy."""
    def row(name, trades, note):
        r = {"scenario": name}
        r.update(core_metrics(trades, cfg.starting_capital))
        r["note"] = note
        return r

    base_trades = base_day_log[base_day_log["traded"]]
    rows = [row("base", base_trades, "frozen config"),
            row("costs_x2_same_trades", reprice_double_costs(base_trades),
                "base trades re-priced with 2x commission and slippage (pure cost stress)")]
    reruns = {
        "costs_x2_reselected": (replace(
            cfg, commission_per_side_usd=cfg.commission_per_side_usd * 2,
            entry_slippage_ticks=cfg.entry_slippage_ticks * 2,
            stop_slippage_ticks=cfg.stop_slippage_ticks * 2,
            time_exit_slippage_ticks=cfg.time_exit_slippage_ticks * 2), cfg.target_r,
            "full re-run at 2x costs; the risk cap then admits fewer trades"),
    }
    other = "target_first" if cfg.ambiguous_bar_rule == "stop_first" else "stop_first"
    reruns[f"ambiguous_{other}"] = (replace(cfg, ambiguous_bar_rule=other), cfg.target_r,
                                    "opposite ambiguous-bar assumption")
    for name, (c, r, note) in reruns.items():
        dl = run_backtest(bars, c, bar_min, r, include_missing_weekdays=False)
        rows.append(row(name, dl[dl["traded"]], note))
    return pd.DataFrame(rows)


def try_plot(trades: pd.DataFrame, cfg: Config, path: Path) -> bool:
    if trades.empty:
        return False
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    eq = cfg.starting_capital + trades["net_usd"].cumsum()
    dd = eq - np.maximum.accumulate(np.r_[cfg.starting_capital, eq.to_numpy()])[1:]
    x = pd.to_datetime(trades["date"])
    fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True, height_ratios=[3, 1])
    ax[0].plot(x, eq)
    ax[0].axhline(cfg.starting_capital, ls="--", lw=0.8)
    ax[0].set_title("MES ORB — equity (after costs)")
    ax[1].fill_between(x, dd, 0)
    ax[1].set_title("Drawdown ($)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return True


def select_period(bars: pd.DataFrame, cfg: Config, period: str) -> pd.DataFrame:
    """Hard wall between development and hold-out data (Blueprint §2.3)."""
    dates = bars["date"]
    if period == "dev":
        keep = (dates >= pd.Timestamp(cfg.dev_start).date()) & (dates <= pd.Timestamp(cfg.dev_end).date())
    elif period == "holdout":
        keep = dates >= pd.Timestamp(cfg.holdout_start).date()
    else:
        raise ValueError(period)
    return bars[keep]


def git_info(script: Path) -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", "-C", str(script.parent), *args], capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    commit = run("rev-parse", "HEAD")
    dirty = run("status", "--porcelain")
    return {"git_commit": commit or None, "git_worktree_dirty": bool(dirty) if commit else None}


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main(argv=None) -> Path:
    ap = argparse.ArgumentParser(description="MES 15-min ORB backtest (v1.1)")
    ap.add_argument("--period", required=True, choices=["dev", "holdout"],
                    help="dev = [dev_start, dev_end], repeatable; holdout = >= holdout_start, ONE use ever")
    ap.add_argument("--data", required=True, help="CSV of intraday bars (see header docs)")
    ap.add_argument("--config", default=None, help="JSON config (toolbox/backtest_config.json)")
    ap.add_argument("--data-tz", default=None, help="tz of naive timestamps, e.g. America/Chicago")
    ap.add_argument("--bar-label", choices=["start", "end"], default="start")
    ap.add_argument("--out", default="results")
    ap.add_argument("--no-sensitivity", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    try:
        bars_all, bar_min, hygiene = load_bars(args.data, args.data_tz, args.bar_label,
                                               cfg.tick_size)
    except DataError as e:
        sys.exit(f"Data error: {e}")
    if cfg.signal_minutes % bar_min != 0:
        sys.exit(f"signal_minutes ({cfg.signal_minutes}) must be a multiple of the bar size ({bar_min}).")

    regimes = compute_regimes(bars_all)       # full history -> lookbacks warmed up
    script = Path(__file__).resolve()
    git = git_info(script)
    bars = select_period(bars_all, cfg, args.period)
    if args.period == "holdout":
        if git["git_commit"] is None or git["git_worktree_dirty"]:
            sys.exit("Refusing the hold-out run: commit all changes first (clean git worktree), so the "
                     "one-time hold-out result is tied to a recorded config + code version.")
        print("*** HOLD-OUT RUN: this is your one use of the hold-out period. Record the run_id in "
              "toolbox/backtest_notes.md whatever the result. ***")
    if cfg.start_date:
        bars = bars[bars["date"] >= pd.Timestamp(cfg.start_date).date()]
    if cfg.end_date:
        bars = bars[bars["date"] <= pd.Timestamp(cfg.end_date).date()]
    bars = bars.reset_index(drop=True)
    if bars.empty:
        sys.exit("No bars left after date filtering.")
    history_before = int((regimes.index < bars["date"].iloc[0]).sum())
    hygiene.update(tested_sessions=int(bars["date"].nunique()),
                   sessions_of_history_before_start=history_before)

    data_hash = file_sha256(args.data)
    script_hash = file_sha256(script)
    identity = json.dumps({"config": asdict(cfg), "period": args.period,
                           "data_tz": args.data_tz, "bar_label": args.bar_label,
                           "script_sha256": script_hash}, sort_keys=True)
    cfg_hash = hashlib.sha256(identity.encode()).hexdigest()[:8]
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{args.period}_cfg{cfg_hash}_data{data_hash[:8]}"
    out = Path(args.out) / run_id
    out.mkdir(parents=True, exist_ok=True)

    day_log = add_regimes(run_backtest(bars, cfg, bar_min), regimes)
    day_log["year"] = pd.to_datetime(day_log["date"]).dt.year
    trades = day_log[day_log["traded"]].copy()
    trades["equity_usd"] = cfg.starting_capital + trades["net_usd"].cumsum()

    summary = full_summary(day_log, cfg)
    summary["monte_carlo"] = monte_carlo(trades["net_usd"].to_numpy(dtype=float), cfg)

    day_log.to_csv(out / "day_log.csv", index=False)
    trades.to_csv(out / "trade_log.csv", index=False)
    trades[["date", "net_usd", "equity_usd"]].to_csv(out / "equity_curve.csv", index=False)
    grouped(day_log, "year").to_csv(out / "by_year.csv", index=False)
    regime_tables = [grouped(day_log, k).rename(columns={k: "regime"}) for k in ("vol_regime", "trend_regime")]
    for k, t in zip(("vol_regime", "trend_regime"), regime_tables):
        t.insert(0, "regime_type", k)
    pd.concat(regime_tables).to_csv(out / "by_regime.csv", index=False)
    if not args.no_sensitivity:
        sensitivity(bars, cfg, bar_min, day_log).to_csv(out / "sensitivity.csv", index=False)
    plotted = try_plot(trades, cfg, out / "equity_drawdown.png")

    warnings = []
    if hygiene["open_alignment"]["months_flagged"]:
        warnings.append("Open-alignment check flagged months where 09:30 ET does not look like the "
                        "cash open — verify --data-tz / --bar-label (see run_manifest.json).")
    if hygiene.get("median_close_warning"):
        warnings.append("Median close is outside 500-20000: is this really ES/MES in index points?")
    if not hygiene["symbol_column"]:
        warnings.append("No symbol column: contract rolls inside a session cannot be detected.")
    if history_before < 272:
        warnings.append(f"Only {history_before} sessions of history before the test start: early "
                        "sessions are labelled 'warmup' in by_regime.csv.")
    if summary["trades"] < 100:
        warnings.append(f"Only {summary['trades']} trades: results are statistically inconclusive "
                        "(Blueprint §3).")
    summary["warnings"] = warnings

    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    manifest = {
        "run_id": run_id, "period": args.period, "script_version": SCRIPT_VERSION,
        "script_sha256": script_hash, **git,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "data_file": str(args.data), "data_sha256": data_hash,
        "data_tz_arg": args.data_tz, "bar_label": args.bar_label,
        "data_hygiene": hygiene, "config": asdict(cfg),
        "python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__,
        "equity_chart": plotted,
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    print(f"\nRun ID : {run_id}\nOutput : {out}")
    print(f"Data   : {hygiene['tested_sessions']} sessions tested, {bar_min}-min bars, "
          f"{bars['date'].iloc[0]} -> {bars['date'].iloc[-1]}")
    print("\n== Summary (after commissions + slippage) ==")
    for k, v in summary.items():
        if k not in ("monte_carlo", "no_trade_reasons", "exit_reasons", "warnings"):
            print(f"  {k:50s} {v}")
    print(f"  {'exit_reasons':50s} {summary.get('exit_reasons')}")
    print(f"  {'no_trade_reasons':50s} {summary['no_trade_reasons']}")
    print(f"  {'monte_carlo':50s} {summary['monte_carlo']}")
    print("\n== By year ==")
    print(pd.read_csv(out / "by_year.csv").to_string(index=False))
    if not args.no_sensitivity:
        print("\n== Sensitivity (robustness only — do not pick the best row) ==")
        print(pd.read_csv(out / "sensitivity.csv").drop(columns="note").to_string(index=False))
    for w in warnings:
        print(f"\nWARNING: {w}")
    if not plotted:
        print("\n(equity chart skipped: no trades or matplotlib not installed; equity_curve.csv written)")
    return out


if __name__ == "__main__":
    main()
