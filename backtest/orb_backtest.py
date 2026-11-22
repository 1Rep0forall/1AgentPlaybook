#!/usr/bin/env python3
"""
MES Opening Range Breakout (ORB) — backtest starter script (v1)
================================================================

WHAT IT DOES
------------
For each regular trading session (RTH, Eastern Time):
  1. Builds the opening range (OR) from bars starting 09:30 up to 09:45 ET.
  2. Stands aside if the OR is incomplete, too narrow, or too wide for the
     per-trade dollar risk cap (no-trade reason is logged for every day).
  3. Looks for the FIRST completed signal bar (default 5-min) that CLOSES
     outside the OR between 09:45 and 11:00 ET. Max 1 trade per session.
  4. Enters at the OPEN of the next base bar + slippage (a market order sent
     right after the signal bar closes), stop at the opposite OR boundary,
     target at a fixed R-multiple, flat at a fixed time-exit cutoff.
  5. Re-checks the all-in dollar risk at the actual entry price; skips if
     above the cap.
  6. Charges commissions + slippage, resolves ambiguous bars conservatively
     (stop first), and fills gaps through the stop at the (worse) bar open.

Outputs (in <out>/<run_id>/): trade_log.csv, day_log.csv (incl. no-trade
days), equity_curve.csv, by_year.csv, by_regime.csv, sensitivity.csv,
summary.json and run_manifest.json (config + data hash = reproducibility).

INPUT DATA FORMAT  (read this before running)
-----------------
CSV with a header row. Required columns (case-insensitive):
    timestamp,open,high,low,close        (volume optional, ignored)

  * timestamp : ISO-8601 text, e.g. "2019-05-06 09:30:00" or
                "2019-05-06T09:30:00-04:00" or "2019-05-06T13:30:00Z".
      - If it carries a UTC offset / "Z", it is converted to America/New_York.
      - If it is naive (no offset) you MUST pass --data-tz, e.g.
        America/Chicago (typical CME vendor data), UTC, or America/New_York.
  * Bar labelling : by default a timestamp marks the bar START
                ("09:30" = 09:30:00-09:30:59). If your vendor stamps the bar
                END, pass --bar-label end.
  * Bar size  : 1-minute bars strongly recommended (fewer ambiguous bars).
                Any bar size that divides 15 minutes works (1, 3, 5, 15).
  * Prices    : a continuous FRONT-MONTH series, either UNADJUSTED (rolled
                on a volume/calendar rule) or ADDITIVE (difference) back-
                adjusted. Do NOT use ratio-adjusted data: it distorts point
                ranges and therefore stop sizes and dollar risk.
  * MES only exists since May 2019. For a 10-year test use ES 1-min data as a
    proxy: point moves are identical, the script applies MES's $5/point.
  * Overnight (ETH) bars may be present; they are ignored (RTH filter).

USAGE
-----
    python backtest/orb_backtest.py --data data/es_1min.csv \
        --data-tz America/Chicago --config toolbox/backtest_config.json \
        --out results

Requires: Python 3.9+, pandas, numpy. matplotlib optional (equity chart).

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
import sys
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

SCRIPT_VERSION = "1.0.0"
ET = "America/New_York"
RTH_START_MIN = 9 * 60 + 30   # 09:30 ET
RTH_END_MIN = 16 * 60         # 16:00 ET


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

    # Exits / risk
    target_r: float = 1.5             # primary (pre-registered) target
    alt_target_r: List[float] = field(default_factory=lambda: [2.0])  # reported, not optimized
    max_risk_usd: float = 25.0        # all-in $ risk cap per trade (stop + slip + fees)
    daily_loss_limit_usd: float = 50.0
    min_or_points: float = 1.0        # skip degenerate/tiny ranges

    # Costs
    commission_per_side_usd: float = 1.00   # commission + exchange + NFA, per contract per side
    entry_slippage_ticks: int = 1
    stop_slippage_ticks: int = 1
    time_exit_slippage_ticks: int = 1
    target_slippage_ticks: int = 0          # limit order
    target_trade_through_ticks: int = 1     # limit counts as filled only if price trades THROUGH it

    # Ambiguity: when stop and target are both inside one bar
    ambiguous_bar_rule: str = "stop_first"  # "stop_first" (conservative) | "target_first"

    # Data hygiene
    min_or_coverage: float = 0.9      # fraction of expected OR bars that must exist

    # Reporting
    drawdown_alert_usd: float = 150.0  # report first date drawdown exceeded this
    start_date: Optional[str] = None   # "YYYY-MM-DD" inclusive
    end_date: Optional[str] = None
    monte_carlo_runs: int = 5000
    random_seed: int = 42


def hhmm_to_min(s: str) -> int:
    hh, mm = s.split(":")
    return int(hh) * 60 + int(mm)


def min_to_hhmm(m: Optional[int]) -> str:
    return "" if m is None else f"{m // 60:02d}:{m % 60:02d}"


def load_config(path: Optional[str]) -> Config:
    cfg = Config()
    if path:
        raw = json.loads(Path(path).read_text())
        valid = {f.name for f in fields(Config)}
        unknown = [k for k in raw if not k.startswith("_") and k not in valid]
        if unknown:  # catches typos that would otherwise silently use defaults
            sys.exit(f"Unknown config keys: {unknown}")
        cfg = replace(cfg, **{k: v for k, v in raw.items() if k in valid})
    if cfg.ambiguous_bar_rule not in ("stop_first", "target_first"):
        sys.exit("ambiguous_bar_rule must be 'stop_first' or 'target_first'")
    if hhmm_to_min(cfg.exit_time) <= hhmm_to_min(cfg.entry_end):
        sys.exit("exit_time must be later than entry_end")
    return cfg


# --------------------------------------------------------------------------
# Data loading & hygiene
# --------------------------------------------------------------------------
def load_bars(path: str, data_tz: Optional[str], bar_label: str):
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in ("timestamp", "open", "high", "low", "close") if c not in df.columns]
    if missing:
        sys.exit(f"Missing required columns: {missing}")

    raw_ts = df["timestamp"].astype(str).str.strip()
    has_offset = raw_ts.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True).mean() > 0.5
    if has_offset:
        ts = pd.to_datetime(raw_ts, utc=True, format="ISO8601").dt.tz_convert(ET)
    else:
        if not data_tz:
            sys.exit("Timestamps have no UTC offset: pass --data-tz (e.g. America/Chicago).")
        ts = pd.to_datetime(raw_ts, format="ISO8601")
        ts = ts.dt.tz_localize(data_tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert(ET)

    out = pd.DataFrame({"ts": ts})
    for c in ("open", "high", "low", "close"):
        out[c] = pd.to_numeric(df[c], errors="coerce")
    report = {"rows_in": int(len(out))}

    out = out.dropna()
    report["rows_dropped_nan_or_bad_time"] = report["rows_in"] - int(len(out))
    out = out.sort_values("ts")
    n = len(out)
    out = out.drop_duplicates("ts", keep="first")
    report["duplicate_timestamps_dropped"] = n - int(len(out))

    bad = (out["high"] < out[["open", "close"]].max(axis=1)) | (out["low"] > out[["open", "close"]].min(axis=1))
    report["bad_ohlc_rows_dropped"] = int(bad.sum())
    out = out[~bad]

    # Infer bar size from the most common spacing between consecutive bars.
    diffs = out["ts"].diff().dt.total_seconds().div(60).dropna()
    bar_min = int(diffs[diffs > 0].mode().iloc[0])
    if 15 % bar_min != 0:
        sys.exit(f"Inferred bar size {bar_min} min does not divide the 15-min opening range.")
    if bar_label == "end":
        out["ts"] = out["ts"] - pd.Timedelta(minutes=bar_min)
    report["bar_minutes"] = bar_min

    out["date"] = out["ts"].dt.date
    out["tod"] = (out["ts"].dt.hour * 60 + out["ts"].dt.minute).astype(int)
    out = out[(out["tod"] >= RTH_START_MIN) & (out["tod"] < RTH_END_MIN)].reset_index(drop=True)

    med = float(out["close"].median())
    if not 500 <= med <= 20000:
        print(f"WARNING: median close {med:.2f} looks wrong for ES/MES index points.")
    report.update(rth_rows=int(len(out)), sessions=int(out["date"].nunique()),
                  first_bar=str(out["ts"].iloc[0]), last_bar=str(out["ts"].iloc[-1]))
    return out, bar_min, report


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Single-session simulation
# --------------------------------------------------------------------------
def first_true(mask: np.ndarray) -> int:
    """Index of first True, or a large sentinel if none."""
    idx = np.flatnonzero(mask)
    return int(idx[0]) if idx.size else 10**9


def simulate_day(date, tod, o, h, l, c, cfg: Config, bar_min: int, target_r: float) -> dict:
    rec = {"date": date, "traded": False, "no_trade_reason": ""}
    tick = cfg.tick_size
    pv = cfg.point_value * cfg.contracts
    rt_comm = 2 * cfg.commission_per_side_usd * cfg.contracts
    ors, ore = hhmm_to_min(cfg.or_start), hhmm_to_min(cfg.or_end)
    es, ee = hhmm_to_min(cfg.entry_start), hhmm_to_min(cfg.entry_end)
    xt = hhmm_to_min(cfg.exit_time)

    # 1) Opening range ----------------------------------------------------
    in_or = (tod >= ors) & (tod < ore)
    if in_or.sum() < cfg.min_or_coverage * (ore - ors) / bar_min:
        rec["no_trade_reason"] = "incomplete_or_data"
        return rec
    or_hi, or_lo = float(h[in_or].max()), float(l[in_or].min())
    width = or_hi - or_lo
    rec.update(or_high=or_hi, or_low=or_lo, or_width_pts=width)
    if width < cfg.min_or_points:
        rec["no_trade_reason"] = "or_too_narrow"
        return rec

    # Cheapest possible trade risk = full OR width + slippage + fees.
    min_risk = (width + (cfg.entry_slippage_ticks + cfg.stop_slippage_ticks) * tick) * pv + rt_comm
    rec["min_risk_usd"] = round(min_risk, 2)
    if min_risk > cfg.max_risk_usd:
        rec["no_trade_reason"] = "or_too_wide_for_risk_cap"
        return rec

    # 2) Signal: first completed N-min bar closing outside the OR -----------
    win = np.flatnonzero((tod >= es) & (tod < ee))
    if win.size == 0:
        rec["no_trade_reason"] = "no_data_in_entry_window"
        return rec
    bucket = (tod[win] - es) // cfg.signal_minutes
    last_in_bucket = np.r_[np.flatnonzero(np.diff(bucket)), bucket.size - 1]
    direction, signal_close_min = 0, None
    for j in last_in_bucket:
        close_min = es + (int(bucket[j]) + 1) * cfg.signal_minutes
        if close_min > ee:
            break
        px = c[win[j]]
        if px > or_hi:
            direction, signal_close_min = 1, close_min
            break
        if px < or_lo:
            direction, signal_close_min = -1, close_min
            break
    if direction == 0:
        rec["no_trade_reason"] = "no_breakout"
        return rec

    # 3) Entry at next bar open + slippage --------------------------------
    after = np.flatnonzero(tod >= signal_close_min)
    if after.size == 0 or tod[after[0]] > ee:
        rec["no_trade_reason"] = "no_entry_bar"
        return rec
    k = int(after[0])
    d = direction
    entry_raw = float(o[k])
    entry = entry_raw + d * cfg.entry_slippage_ticks * tick
    stop = or_lo if d == 1 else or_hi
    risk_pts = (entry - stop) * d
    if risk_pts <= 0:
        rec["no_trade_reason"] = "entry_beyond_stop"
        return rec
    risk_usd = (risk_pts + cfg.stop_slippage_ticks * tick) * pv + rt_comm
    if risk_usd > cfg.max_risk_usd:
        rec.update(no_trade_reason="risk_cap_at_entry", min_risk_usd=round(risk_usd, 2))
        return rec
    # Snap target to the tick grid, rounding AWAY from entry (conservative).
    raw_target = entry + d * target_r * risk_pts
    target = (np.ceil(raw_target / tick) if d == 1 else np.floor(raw_target / tick)) * tick

    # 4) Manage the trade bar by bar until the time exit -------------------
    k_exit = int(np.searchsorted(tod, xt, side="left"))  # first bar at/after exit time
    seg = slice(k, k_exit)
    oo, hh, ll = o[seg], h[seg], l[seg]
    thru = cfg.target_trade_through_ticks * tick
    if d == 1:
        si, ti = first_true(ll <= stop), first_true(hh >= target + thru)
    else:
        si, ti = first_true(hh >= stop), first_true(ll <= target - thru)

    ambiguous = si == ti and si < 10**9
    if si == ti == 10**9:  # neither hit: time exit
        if k_exit < len(tod):
            exit_raw, exit_min, reason = float(o[k_exit]), int(tod[k_exit]), "time_exit"
        else:  # early close / data ends before cutoff
            exit_raw, exit_min, reason = float(c[-1]), int(tod[-1]) + bar_min, "data_end_exit"
        exit_slip, last = cfg.time_exit_slippage_ticks, len(oo) - 1
    elif si < ti or (ambiguous and cfg.ambiguous_bar_rule == "stop_first"):
        gapped = (oo[si] <= stop) if d == 1 else (oo[si] >= stop)
        gapped = gapped and si > 0  # entry bar opens at our entry, beyond the stop by construction
        exit_raw = float(oo[si]) if gapped else stop
        exit_min, reason = int(tod[k + si]), ("stop_gap" if gapped else "stop")
        exit_slip, last = cfg.stop_slippage_ticks, si
    else:
        exit_raw, exit_min, reason = target, int(tod[k + ti]), "target"
        exit_slip, last = cfg.target_slippage_ticks, ti
    exit_px = exit_raw - d * exit_slip * tick

    pnl_pts = (exit_px - entry) * d
    gross = pnl_pts * pv
    net = gross - rt_comm
    if d == 1:
        mae, mfe = entry - ll[: last + 1].min(), hh[: last + 1].max() - entry
    else:
        mae, mfe = hh[: last + 1].max() - entry, entry - ll[: last + 1].min()

    rec.update(
        traded=True, direction="long" if d == 1 else "short",
        signal_bar_close=min_to_hhmm(signal_close_min), entry_time=min_to_hhmm(int(tod[k])),
        entry_price=entry, stop_price=stop, target_price=target,
        risk_pts=risk_pts, risk_usd=round(risk_usd, 2),
        exit_time=min_to_hhmm(exit_min), exit_price=exit_px, exit_reason=reason,
        ambiguous_bar=bool(ambiguous),
        pnl_pts=pnl_pts, gross_usd=round(gross, 2), commission_usd=rt_comm,
        slippage_usd=(cfg.entry_slippage_ticks + exit_slip) * tick * pv,
        net_usd=round(net, 2), r_multiple=round(net / risk_usd, 3),
        mae_pts=float(max(mae, 0)), mfe_pts=float(max(mfe, 0)),
        daily_limit_breach=bool(net < -cfg.daily_loss_limit_usd),
    )
    return rec


TRADE_COLUMNS = [
    "or_high", "or_low", "or_width_pts", "min_risk_usd", "direction", "signal_bar_close",
    "entry_time", "entry_price", "stop_price", "target_price", "risk_pts", "risk_usd",
    "exit_time", "exit_price", "exit_reason", "ambiguous_bar", "pnl_pts", "gross_usd",
    "commission_usd", "slippage_usd", "net_usd", "r_multiple", "mae_pts", "mfe_pts",
    "daily_limit_breach",
]


def run_backtest(bars: pd.DataFrame, cfg: Config, bar_min: int, target_r: Optional[float] = None) -> pd.DataFrame:
    target_r = cfg.target_r if target_r is None else target_r
    dates = bars["date"].to_numpy()
    tod = bars["tod"].to_numpy()
    o, h, l, c = (bars[x].to_numpy(dtype=float) for x in ("open", "high", "low", "close"))
    _, starts = np.unique(dates, return_index=True)
    ends = np.r_[starts[1:], len(dates)]
    rows = []
    for s, e in zip(starts, ends):
        # 1 trade max per session is enforced structurally: one call per day.
        rows.append(simulate_day(dates[s], tod[s:e], o[s:e], h[s:e], l[s:e], c[s:e], cfg, bar_min, target_r))
    df = pd.DataFrame(rows)
    # Guarantee a stable schema even when zero trades occur (common with tight risk caps).
    for col in TRADE_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    df["traded"] = df["traded"].astype(bool)
    return df


# --------------------------------------------------------------------------
# Regimes (computed ONLY from information available before each session)
# --------------------------------------------------------------------------
def add_regimes(day_log: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    daily = bars.groupby("date").agg(o=("open", "first"), h=("high", "max"),
                                     l=("low", "min"), c=("close", "last"))
    prev_c = daily["c"].shift(1)
    tr = pd.concat([daily["h"] - daily["l"], (daily["h"] - prev_c).abs(),
                    (daily["l"] - prev_c).abs()], axis=1).max(axis=1)
    atr_pct = (tr.rolling(20).mean() / daily["c"]).shift(1)        # prior 20 days, % of price
    vol_rank = atr_pct.rolling(252).rank(pct=True)                   # vs trailing year
    vol = pd.cut(vol_rank, [0, 1 / 3, 2 / 3, 1.0], labels=["low_vol", "mid_vol", "high_vol"],
                 include_lowest=True).astype(object).fillna("warmup")
    sma200 = daily["c"].rolling(200).mean().shift(1)
    trend = np.where(sma200.isna(), "warmup", np.where(prev_c > sma200, "uptrend", "downtrend"))
    reg = pd.DataFrame({"vol_regime": vol, "trend_regime": trend}, index=daily.index)
    return day_log.merge(reg, left_on="date", right_index=True, how="left")


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
        "net_pnl_usd": round(pnl.sum(), 2),
        "profit_factor": round(wins.sum() / gl, 2) if gl > 0 else None,
        "expectancy_usd": round(pnl.mean(), 2) if n else None,
        "expectancy_r": round(trades["r_multiple"].mean(), 3) if n else None,
        "max_drawdown_usd": round(max_drawdown(pnl, start), 2),
        "longest_losing_streak": longest_streak(pnl <= 0),
    }


def full_summary(day_log: pd.DataFrame, cfg: Config) -> dict:
    trades = day_log[day_log["traded"]]
    pnl = trades["net_usd"].to_numpy(dtype=float)
    s = {"sessions": int(len(day_log)), "trade_rate": round(len(trades) / max(len(day_log), 1), 3)}
    s.update(core_metrics(trades, cfg.starting_capital))
    if len(trades):
        eq = cfg.starting_capital + np.cumsum(pnl)
        peak = np.maximum.accumulate(np.r_[cfg.starting_capital, eq])[1:]
        breach = np.flatnonzero(peak - eq >= cfg.drawdown_alert_usd)
        s.update(
            avg_win_usd=round(pnl[pnl > 0].mean(), 2) if (pnl > 0).any() else None,
            avg_loss_usd=round(pnl[pnl <= 0].mean(), 2) if (pnl <= 0).any() else None,
            gross_pnl_before_costs_usd=round(trades["gross_usd"].sum()
                                             + trades["slippage_usd"].sum(), 2),
            total_commission_usd=round(trades["commission_usd"].sum(), 2),
            total_slippage_usd=round(trades["slippage_usd"].sum(), 2),
            max_drawdown_pct_of_start=round(100 * s["max_drawdown_usd"] / cfg.starting_capital, 1),
            ending_equity_usd=round(eq[-1], 2),
            lowest_equity_usd=round(min(eq.min(), cfg.starting_capital), 2),
            first_date_dd_exceeded_alert=str(trades["date"].iloc[breach[0]]) if breach.size else None,
            exit_reasons=trades["exit_reason"].value_counts().to_dict(),
            ambiguous_bars=int(trades["ambiguous_bar"].sum()),
            ambiguous_bar_share=round(trades["ambiguous_bar"].mean(), 3),
            daily_loss_limit_breaches=int(trades["daily_limit_breach"].sum()),
            longest_flat_or_losing_streak_sessions=longest_streak(
                ~(day_log["traded"] & (day_log["net_usd"].fillna(0) > 0)).to_numpy()),
        )
    s["no_trade_reasons"] = day_log.loc[~day_log["traded"], "no_trade_reason"].value_counts().to_dict()
    return s


def grouped(day_log: pd.DataFrame, key: str) -> pd.DataFrame:
    rows = []
    for k, g in day_log.groupby(key, sort=True):
        t = g[g["traded"]]
        r = {key: k, "sessions": len(g), "no_trade_days": int((~g["traded"]).sum())}
        r.update(core_metrics(t, 0.0))  # drawdown measured from the start of each bucket
        rows.append(r)
    return pd.DataFrame(rows)


def monte_carlo(pnl: np.ndarray, cfg: Config) -> dict:
    """Bootstrap trade order (assumes trades are independent — they may not be)."""
    if pnl.size < 10:
        return {"note": "fewer than 10 trades; Monte Carlo skipped"}
    rng = np.random.default_rng(cfg.random_seed)
    sims = rng.choice(pnl, size=(cfg.monte_carlo_runs, pnl.size), replace=True)
    eq = np.cumsum(sims, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((sims.shape[0], 1)), eq], axis=1), axis=1)[:, 1:]
    mdd = (peak - eq).max(axis=1)
    loss = sims <= 0
    idx = np.arange(pnl.size)
    last_reset = np.maximum.accumulate(np.where(~loss, idx, -1), axis=1)
    streak = (idx - last_reset).max(axis=1)
    half = cfg.starting_capital / 2
    return {
        "runs": cfg.monte_carlo_runs,
        "max_dd_usd_p50": round(float(np.percentile(mdd, 50)), 2),
        "max_dd_usd_p95": round(float(np.percentile(mdd, 95)), 2),
        "losing_streak_p50": int(np.percentile(streak, 50)),
        "losing_streak_p95": int(np.percentile(streak, 95)),
        "prob_dd_ge_half_account": round(float((mdd >= half).mean()), 3),
        "prob_net_loss": round(float((eq[:, -1] < 0).mean()), 3),
    }


def sensitivity(bars, cfg: Config, bar_min: int) -> pd.DataFrame:
    """Pre-declared robustness checks. Do NOT pick the best row and call it the strategy."""
    scenarios = {"base": (cfg, cfg.target_r)}
    scenarios["costs_x2"] = (replace(
        cfg, commission_per_side_usd=cfg.commission_per_side_usd * 2,
        entry_slippage_ticks=cfg.entry_slippage_ticks * 2,
        stop_slippage_ticks=cfg.stop_slippage_ticks * 2,
        time_exit_slippage_ticks=cfg.time_exit_slippage_ticks * 2), cfg.target_r)
    other = "target_first" if cfg.ambiguous_bar_rule == "stop_first" else "stop_first"
    scenarios[f"ambiguous_{other}"] = (replace(cfg, ambiguous_bar_rule=other), cfg.target_r)
    for r in cfg.alt_target_r:
        scenarios[f"target_{r}R"] = (cfg, r)
    rows = []
    for name, (c, r) in scenarios.items():
        dl = run_backtest(bars, c, bar_min, r)
        row = {"scenario": name}
        row.update(core_metrics(dl[dl["traded"]], c.starting_capital))
        rows.append(row)
    return pd.DataFrame(rows)


def try_plot(day_log: pd.DataFrame, cfg: Config, path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    t = day_log[day_log["traded"]]
    eq = cfg.starting_capital + t["net_usd"].cumsum()
    dd = eq - np.maximum.accumulate(np.r_[cfg.starting_capital, eq.to_numpy()])[1:]
    fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True, height_ratios=[3, 1])
    ax[0].plot(pd.to_datetime(t["date"]), eq)
    ax[0].axhline(cfg.starting_capital, ls="--", lw=0.8)
    ax[0].set_title("MES ORB v1 — equity (after costs)")
    ax[1].fill_between(pd.to_datetime(t["date"]), dd, 0)
    ax[1].set_title("Drawdown ($)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return True


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="MES 15-min ORB backtest (v1)")
    ap.add_argument("--data", required=True, help="CSV of intraday bars (see header docs)")
    ap.add_argument("--config", default=None, help="JSON config (toolbox/backtest_config.json)")
    ap.add_argument("--data-tz", default=None, help="tz of naive timestamps, e.g. America/Chicago")
    ap.add_argument("--bar-label", choices=["start", "end"], default="start")
    ap.add_argument("--out", default="results")
    ap.add_argument("--no-sensitivity", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    bars, bar_min, hygiene = load_bars(args.data, args.data_tz, args.bar_label)
    if cfg.start_date:
        bars = bars[bars["date"] >= pd.Timestamp(cfg.start_date).date()]
    if cfg.end_date:
        bars = bars[bars["date"] <= pd.Timestamp(cfg.end_date).date()]
    bars = bars.reset_index(drop=True)
    if bars.empty:
        sys.exit("No bars left after date filtering.")

    data_hash = file_sha256(args.data)
    cfg_json = json.dumps(asdict(cfg), sort_keys=True)
    cfg_hash = hashlib.sha256(cfg_json.encode()).hexdigest()[:8]
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_cfg{cfg_hash}_data{data_hash[:8]}"
    out = Path(args.out) / run_id
    out.mkdir(parents=True, exist_ok=True)

    day_log = add_regimes(run_backtest(bars, cfg, bar_min), bars)
    day_log["year"] = pd.to_datetime(day_log["date"]).dt.year
    trades = day_log[day_log["traded"]].copy()
    trades["equity_usd"] = cfg.starting_capital + trades["net_usd"].cumsum()

    summary = full_summary(day_log, cfg)
    summary["monte_carlo"] = monte_carlo(trades["net_usd"].to_numpy(dtype=float), cfg)

    day_log.to_csv(out / "day_log.csv", index=False)
    trades.to_csv(out / "trade_log.csv", index=False)
    trades[["date", "net_usd", "equity_usd"]].to_csv(out / "equity_curve.csv", index=False)
    grouped(day_log, "year").to_csv(out / "by_year.csv", index=False)
    regimes = pd.concat([
        grouped(day_log, k).rename(columns={k: "regime"}).assign(regime_type=k).set_index("regime_type").reset_index()
        for k in ("vol_regime", "trend_regime")])
    regimes.to_csv(out / "by_regime.csv", index=False)
    if not args.no_sensitivity:
        sensitivity(bars, cfg, bar_min).to_csv(out / "sensitivity.csv", index=False)
    plotted = try_plot(day_log, cfg, out / "equity_drawdown.png")

    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    manifest = {
        "run_id": run_id, "script_version": SCRIPT_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "data_file": str(args.data), "data_sha256": data_hash,
        "data_tz_arg": args.data_tz, "bar_label": args.bar_label,
        "data_hygiene": hygiene, "config": asdict(cfg),
        "python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__,
        "equity_chart": plotted,
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    print(f"\nRun ID : {run_id}\nOutput : {out}")
    print(f"Data   : {hygiene['sessions']} sessions, {bar_min}-min bars, "
          f"{hygiene['first_bar']} -> {hygiene['last_bar']}")
    print("\n== Summary (after commissions + slippage) ==")
    for k, v in summary.items():
        if k not in ("monte_carlo", "no_trade_reasons", "exit_reasons"):
            print(f"  {k:40s} {v}")
    print(f"  {'exit_reasons':40s} {summary.get('exit_reasons')}")
    print(f"  {'no_trade_reasons':40s} {summary['no_trade_reasons']}")
    print(f"  {'monte_carlo':40s} {summary['monte_carlo']}")
    print("\n== By year ==")
    print(pd.read_csv(out / "by_year.csv").to_string(index=False))
    if not args.no_sensitivity:
        print("\n== Sensitivity (robustness only — do not pick the best row) ==")
        print(pd.read_csv(out / "sensitivity.csv").to_string(index=False))
    if not plotted:
        print("\n(matplotlib not installed: equity chart skipped; equity_curve.csv written)")


if __name__ == "__main__":
    main()
