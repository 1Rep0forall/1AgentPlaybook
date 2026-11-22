"""Unit tests for backtest/orb_backtest.py — hand-built sessions with known answers.

Run:  python -m pytest tests/ -q
"""
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backtest"))
import orb_backtest as ob  # noqa: E402
import session_tools as st  # noqa: E402

CFG = ob.Config()


# ---------------------------------------------------------------- helpers
def m(hhmm):
    return ob.hhmm_to_min(hhmm)


def hhmm(minute):
    return ob.min_to_hhmm(minute)


def or_bars(width=3.0, lo=4000.0):
    """15 one-minute opening-range bars spanning [lo, lo+width]."""
    mid = lo + 1.0 if width >= 1.0 else lo
    return [(hhmm(t), mid, lo + width, lo, mid) for t in range(m("09:30"), m("09:45"))]


def flat(start, end, px):
    return [(hhmm(t), px, px, px, px) for t in range(m(start), m(end))]


def session(rows):
    rows = sorted(rows, key=lambda r: m(r[0]))
    tod = np.array([m(r[0]) for r in rows])
    a = np.array([r[1:] for r in rows], dtype=float)
    return tod, a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def sim(rows, **cfg_overrides):
    cfg = replace(CFG, **cfg_overrides)
    ob.validate_config(cfg)
    t, o, h, l, c = session(rows)
    return ob.simulate_day("2024-01-02", t, o, h, l, c, cfg, 1, cfg.target_r)


def long_breakout(width=2.0, lo=4000.0, close_above=0.25, rest=None):
    """OR, then a 09:45-09:49 signal bar closing `close_above` pts over the OR high."""
    hi = lo + width
    sig = [(hhmm(t), hi - 0.25, hi + close_above, hi - 0.5, hi + close_above)
           for t in range(m("09:45"), m("09:50"))]
    rest = rest if rest is not None else flat("09:50", "12:05", hi + close_above)
    return or_bars(width, lo) + sig + rest


# ---------------------------------------------------------------- range & cap rules
def test_min_or_rule_is_strictly_less_than():
    assert sim(or_bars(0.75) + flat("09:45", "12:05", 4000.5))["no_trade_reason"] == "or_too_narrow"
    assert sim(long_breakout(width=1.0))["no_trade_reason"] != "or_too_narrow"


def test_precheck_includes_one_tick_breakout():
    # (3.75 + 3 ticks) * 5 + 2 = 24.5 -> passes; 4.0 -> 25.75 -> rejected
    r = sim(long_breakout(width=3.75))
    assert r["min_risk_usd"] == 24.5 and r["no_trade_reason"] != "or_too_wide_for_risk_cap"
    r = sim(long_breakout(width=4.0))
    assert r["min_risk_usd"] == 25.75 and r["no_trade_reason"] == "or_too_wide_for_risk_cap"


def test_capped_limit_price_and_no_fill_when_open_is_past_it():
    # stop 4000; allowed = (25-2)/5 - 0.25 = 4.35 -> 17 ticks -> limit 4004.25
    assert ob.risk_capped_limit(4000.0, 1, CFG) == 4004.25
    assert ob.risk_capped_limit(4010.0, -1, CFG) == 4005.75
    rows = long_breakout(width=3.0, close_above=1.5, rest=flat("09:50", "12:05", 4004.5))
    r = sim(rows)
    assert r["no_trade_reason"] == "entry_limit_not_filled" and r["entry_limit_price"] == 4004.25


def test_capped_limit_never_fills_worse_than_limit():
    rows = long_breakout(width=3.0, close_above=1.25, rest=flat("09:50", "12:05", 4004.25))
    r = sim(rows)
    assert r["traded"] and r["entry_price"] == 4004.25      # slippage capped at the limit
    assert r["risk_usd"] <= CFG.max_risk_usd


# ---------------------------------------------------------------- exits
def test_ambiguous_bar_stop_first_vs_target_first():
    rest = [("09:50", 4002.25, 4020, 3990, 4000)] + flat("09:51", "12:05", 4000)
    rows = long_breakout(width=2.0, rest=rest)
    a, b = sim(rows), sim(rows, ambiguous_bar_rule="target_first")
    assert a["ambiguous_bar"] and a["exit_reason"] == "stop" and a["r_multiple"] == -1.0
    assert b["exit_reason"] == "target" and b["r_multiple"] > 0


def test_gap_through_stop_fills_at_worse_open_and_flags_daily_limit():
    rest = [("09:50", 4002.25, 4003, 4002, 4002.5), ("09:51", 3990, 3991, 3989, 3990)] + flat("09:52", "12:05", 3990)
    r = sim(long_breakout(width=2.0, rest=rest))
    assert r["exit_reason"] == "stop_gap" and r["exit_price"] == 3989.75
    assert r["daily_limit_breach"]


def test_target_requires_trade_through_and_rounds_away_from_entry():
    # entry 4002.5 (open 4002.25 + 1 tick), stop 4000, risk 2.5 -> target 4006.25
    hit = [("09:50", 4002.25, 4002.5, 4002.25, 4002.5), ("09:51", 4002.5, 4006.5, 4002.5, 4006)] + flat("09:52", "12:05", 4006)
    touch = [("09:50", 4002.25, 4002.5, 4002.25, 4002.5), ("09:51", 4002.5, 4006.25, 4002.5, 4006)] + flat("09:52", "12:05", 4006)
    a = sim(long_breakout(width=2.0, rest=hit))
    b = sim(long_breakout(width=2.0, rest=touch))
    assert a["target_price"] == 4006.25 and a["exit_reason"] == "target"
    assert b["exit_reason"] == "time_exit"          # touch only, no fill


def test_time_exit_and_data_end_exit():
    r = sim(long_breakout(width=2.0))
    assert r["exit_reason"] == "time_exit" and r["exit_time"] == "12:00"
    early = long_breakout(width=2.0, rest=flat("09:50", "11:30", 4002.25))
    assert sim(early)["exit_reason"] == "data_end_exit"


def test_short_side_mirror():
    lo, hi = 4000.0, 4002.0
    sig = [(hhmm(t), lo + 0.25, lo + 0.5, lo - 0.5, lo - 0.25) for t in range(m("09:45"), m("09:50"))]
    rest = [("09:50", 3999.75, 4002.5, 3999.5, 4002)] + flat("09:51", "12:05", 4002)
    r = sim(or_bars(2.0, lo) + sig + rest)
    assert r["direction"] == "short" and r["stop_price"] == hi
    assert r["entry_price"] == 3999.5 and r["exit_reason"] == "stop" and r["r_multiple"] == -1.0


# ---------------------------------------------------------------- data gaps
def test_incomplete_or_and_incomplete_signal_bar():
    rows = [r for r in long_breakout(width=2.0) if r[0] != "09:40"]
    assert sim(rows)["no_trade_reason"] == "incomplete_or_data"
    rows = [r for r in long_breakout(width=2.0) if r[0] != "09:49"]
    assert sim(rows)["no_trade_reason"] == "incomplete_signal_data"


def test_every_emitted_reason_is_registered():
    cases = [or_bars(0.75) + flat("09:45", "12:05", 4000.5), long_breakout(width=4.0),
             or_bars(2.0) + flat("09:45", "12:05", 4001)]
    for rows in cases:
        reason = sim(rows)["no_trade_reason"]
        assert reason in ob.REASON_CODES


# ---------------------------------------------------------------- loading
def write_csv(tmp_path, rows, name="bars.csv"):
    p = tmp_path / name
    pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"]).to_csv(p, index=False)
    return p


def test_naive_central_time_end_labelled_bars_map_to_0930_et(tmp_path):
    # 08:31 CT bar END == 09:30 ET bar START (January, no DST)
    p = write_csv(tmp_path, [("2024-01-02 08:31:00", 1, 1, 1, 1), ("2024-01-02 08:32:00", 1, 1, 1, 1)])
    bars, bar_min, _ = ob.load_bars(str(p), "America/Chicago", "end")
    assert bar_min == 1 and list(bars["tod"]) == [m("09:30"), m("09:31")]


def test_mixed_offset_file_is_rejected(tmp_path):
    p = write_csv(tmp_path, [("2024-01-02T09:30:00-05:00", 1, 1, 1, 1), ("2024-01-02 09:31:00", 1, 1, 1, 1)])
    with pytest.raises(ob.DataError, match="offset"):
        ob.load_bars(str(p), "America/New_York", "start")


def test_off_grid_prices_rejected(tmp_path):
    rows = [(f"2024-01-02T09:{mm:02d}:00-05:00", 4000.1, 4000.1, 4000.1, 4000.1) for mm in range(30, 40)]
    with pytest.raises(ob.DataError, match="tick grid"):
        ob.load_bars(str(write_csv(tmp_path, rows)), None, "start")


def test_conflicting_duplicates_counted(tmp_path):
    rows = [("2024-01-02T09:30:00-05:00", 1, 1, 1, 1), ("2024-01-02T09:30:00-05:00", 2, 2, 2, 2),
            ("2024-01-02T09:31:00-05:00", 1, 1, 1, 1)]
    _, _, rep = ob.load_bars(str(write_csv(tmp_path, rows)), None, "start")
    assert rep["duplicate_timestamps_dropped"] == 1
    assert rep["duplicate_timestamps_with_conflicting_prices"] == 1


# ---------------------------------------------------------------- portfolio-level behaviour
def bars_frame(day_rows: dict) -> pd.DataFrame:
    frames = []
    for date, rows in day_rows.items():
        t, o, h, l, c = session(rows)
        frames.append(pd.DataFrame({"date": pd.Timestamp(date).date(), "tod": t,
                                    "open": o, "high": h, "low": l, "close": c}))
    return pd.concat(frames, ignore_index=True)


def test_missing_weekdays_and_roll_sessions_are_logged():
    bars = bars_frame({"2024-01-02": long_breakout(2.0), "2024-01-04": long_breakout(2.0)})
    bars["symbol"] = "ESH24"
    bars.loc[(bars["date"] == pd.Timestamp("2024-01-04").date()) & (bars["tod"] >= m("10:00")), "symbol"] = "ESM24"
    dl = ob.run_backtest(bars, CFG, 1)
    reasons = dict(zip(dl["date"].astype(str), dl["no_trade_reason"]))
    assert reasons["2024-01-03"] == "no_session_data"
    assert reasons["2024-01-04"] == "contract_roll_in_session"


def test_regimes_computed_on_full_history_not_filtered_range():
    days = pd.bdate_range("2023-01-02", periods=300)
    rng = np.random.default_rng(0)
    frames = []
    for i, d in enumerate(days):
        px = 4000 + i
        frames.append({"date": d.date(), "tod": m("09:30"), "open": px, "high": px + rng.uniform(1, 9),
                       "low": px - rng.uniform(1, 9), "close": px})
    bars = pd.DataFrame(frames)
    reg = ob.compute_regimes(bars)
    assert (reg["vol_regime"].iloc[-10:] != "warmup").all()
    # filtering first would leave everything in warmup — the old bug
    assert (ob.compute_regimes(bars.iloc[-10:])["vol_regime"] == "warmup").all()


def test_costs_x2_same_trades_keeps_trade_count():
    trades = pd.DataFrame({"net_usd": [10.0, -20.0], "commission_usd": [2.0, 2.0],
                           "slippage_usd": [1.25, 2.5], "risk_usd": [20.0, 20.0]})
    t = ob.reprice_double_costs(trades)
    assert len(t) == 2 and list(t["net_usd"]) == [6.75, -24.5]


# ---------------------------------------------------------------- docs stay in sync with code
def test_playbook_lists_every_reason_code():
    text = (ROOT / "playbook" / "MES_ORB_Playbook_v1.md").read_text()
    missing = [c for c in {**ob.REASON_CODES, **ob.LIVE_ONLY_REASON_CODES} if f"`{c}`" not in text]
    assert not missing, f"playbook no-trade table is missing {missing}"


def test_trade_log_template_has_shared_columns():
    header = (ROOT / "toolbox" / "trade_log_template.csv").read_text().splitlines()[0].split(",")
    missing = [c for c in ob.SHARED_LOG_COLUMNS if c not in header]
    assert not missing, f"trade_log_template.csv is missing {missing}"


def test_config_loads_and_version_is_labelled():
    cfg = ob.load_config(str(ROOT / "toolbox" / "backtest_config.json"))
    assert cfg.max_risk_usd == 25.0 and cfg.dev_end < cfg.holdout_start
    raw = json.loads((ROOT / "toolbox" / "backtest_config.json").read_text())
    assert re.match(r"v1\.1", raw["_version"])


def test_docs_quote_the_numbers_derived_from_config():
    """Root-cause guard: if the cap, fees or slippage change, the playbook and
    checklist numbers must be updated in the same commit."""
    cfg = ob.load_config(str(ROOT / "toolbox" / "backtest_config.json"))
    n = ob.derived_numbers(cfg)
    assert (n["limit_offset_pts"], n["max_or_width_pts"], n["precheck_extra_pts"]) == (4.25, 3.75, 0.75)
    for doc in ("playbook/MES_ORB_Playbook_v1.md", "toolbox/session_checklist.md"):
        text = (ROOT / doc).read_text()
        for needle in (f"OR Low + {n['limit_offset_pts']}", f"OR High − {n['limit_offset_pts']}",
                       f"(width + {n['precheck_extra_pts']})"):
            assert needle in text, f"{doc} does not quote '{needle}' (derived from config)"


# ---------------------------------------------------------------- hold-out governance
def test_period_selection_is_a_hard_wall():
    bars = bars_frame({"2021-12-31": long_breakout(2.0), "2022-01-03": long_breakout(2.0)})
    assert set(ob.select_period(bars, CFG, "dev")["date"].astype(str)) == {"2021-12-31"}
    assert set(ob.select_period(bars, CFG, "holdout")["date"].astype(str)) == {"2022-01-03"}


def five_day_csv(tmp_path, start):
    rows = []
    for d in pd.bdate_range(start, periods=5):
        for tm, o, h, l, c in long_breakout(2.0):
            rows.append((f"{d:%Y-%m-%d}T{tm}:00-05:00", o, h, l, c))
    return write_csv(tmp_path, rows)


def test_period_flag_is_required(tmp_path):
    with pytest.raises(SystemExit):
        ob.main(["--data", str(five_day_csv(tmp_path, "2016-01-04")), "--out", str(tmp_path / "res")])


def test_holdout_refused_on_dirty_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "git_info", lambda _: {"git_commit": "abc", "git_worktree_dirty": True})
    with pytest.raises(SystemExit, match="clean git worktree"):
        ob.main(["--period", "holdout", "--data", str(five_day_csv(tmp_path, "2024-01-02")),
                 "--out", str(tmp_path / "res")])


# ---------------------------------------------------------------- session tools
def log_frame(rows):
    cols = (ROOT / "toolbox" / "trade_log_template.csv").read_text().splitlines()[0].split(",")
    df = pd.DataFrame([{c: "" for c in cols} | r for r in rows])
    return df.astype(str)


def traded_row(date, net, violation="N"):
    return {"date": date, "mode": "SIM", "traded": "True", "entry_price": "4002.5", "stop_price": "4000",
            "exit_price": "4000", "exit_reason": "stop", "net_usd": str(net), "rule_violation": violation,
            "chart_screenshot_path": "logs/charts/x.png"}


def test_status_escalates_on_drawdown_and_flags_missing_proof():
    rows = [traded_row(f"2026-01-{d:02d}", -25) for d in range(5, 9)]          # -$100 -> pause
    s = st.status(log_frame(rows), CFG)
    assert s["verdict"] == "PAUSE" and s["current_losing_streak"] == 4 and s["drawdown_usd"] == 100
    rows += [traded_row("2026-01-09", -25), traded_row("2026-01-12", -25)]   # -$150 -> stop
    assert st.status(log_frame(rows), CFG)["verdict"] == "STOP"
    gap = [{"date": "2026-01-05", "mode": "SIM", "traded": "False", "no_trade_reason": "made_up_code",
            "rule_violation": "N"}]
    s = st.status(log_frame(gap), CFG)
    assert s["verdict"] == "FIX-PROOF" and "no_trade_reason" in s["proof_gaps"][0]


def test_status_go_and_no_trade_streak():
    rows = [{"date": f"2026-01-0{d}", "mode": "SIM", "traded": "False",
             "no_trade_reason": "or_too_wide_for_risk_cap", "rule_violation": "N"} for d in range(5, 9)]
    s = st.status(log_frame(rows), CFG)
    assert s["verdict"] == "GO" and s["current_no_trade_streak"] == 4


def test_reconcile_classifies_differences():
    model = sim(long_breakout(width=2.0))                        # time-exit trade
    logged = pd.Series({k: str(v) for k, v in model.items()})
    assert st.reconcile(model, logged, CFG)["verdict"] == "MATCH"
    worse = logged.copy()
    worse["entry_price"] = str(model["entry_price"] + 0.75)
    assert st.reconcile(model, worse, CFG)["verdict"] == "FILL_DIFF"
    stood_aside = pd.Series({"traded": "False", "no_trade_reason": "no_breakout"})
    assert st.reconcile(model, stood_aside, CFG)["verdict"] == "DECISION_MISMATCH"


def test_rules_output_matches_derived_numbers():
    r = st.rules(CFG)
    assert r["long_limit"] == "OR Low + 4.25" and r["max_or_width_pts"] == 3.75


# ---------------------------------------------------------------- end to end
def test_end_to_end_smoke(tmp_path):
    p = five_day_csv(tmp_path, "2016-01-04")
    out = ob.main(["--period", "dev", "--data", str(p), "--out", str(tmp_path / "res")])
    for f in ("trade_log.csv", "day_log.csv", "summary.json", "run_manifest.json",
              "by_year.csv", "by_regime.csv", "sensitivity.csv", "equity_curve.csv"):
        assert (out / f).exists(), f
    man = json.loads((out / "run_manifest.json").read_text())
    assert man["script_sha256"] and "git_commit" in man and man["period"] == "dev"
    summ = json.loads((out / "summary.json").read_text())
    assert summ["trades"] == 5 and summ["warnings"]
