"""Tests for backtest/gates.py (Blueprint §4 evaluator) and session_tools log (validated log rows)."""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backtest"))
import gates as gt  # noqa: E402
import orb_backtest as ob  # noqa: E402
import session_tools as st  # noqa: E402

CFG = ob.load_config(str(ROOT / "toolbox" / "backtest_config.json"))


# ------------------------------------------------------------------ gates
def fake_run(tmp, name, period, trades, exp, pf, x2_exp, amb_exp, years, dirty=False, pnl=None):
    d = tmp / name
    d.mkdir()
    (d / "summary.json").write_text(json.dumps({"trades": trades, "expectancy_usd": exp, "profit_factor": pf,
                                                "warnings": []}))
    from dataclasses import asdict
    (d / "run_manifest.json").write_text(json.dumps({"period": period, "git_worktree_dirty": dirty,
                                                     "config": asdict(CFG)}))
    pd.DataFrame([{"year": y, "trades": t, "net_pnl_usd": v} for y, t, v in years]).to_csv(d / "by_year.csv", index=False)
    pd.DataFrame({"net_usd": pnl if pnl is not None else [exp] * trades}).to_csv(d / "trade_log.csv", index=False)
    pd.DataFrame([{"scenario": "base", "expectancy_usd": exp},
                  {"scenario": "costs_x2_same_trades", "expectancy_usd": x2_exp},
                  {"scenario": "ambiguous_target_first", "expectancy_usd": amb_exp}]).to_csv(d / "sensitivity.csv", index=False)
    return d


def good_pnl(n):
    return [12.0 if i % 2 else -6.0 for i in range(n)]   # +$3/trade average, small drawdowns


def test_dev_only_fail_stops_before_holdout(tmp_path):
    dev = fake_run(tmp_path, "d", "dev", 80, -1.0, 0.9, -3.0, -1.0, [(2016, 80, -80)])
    r = gt.evaluate(dev)
    assert r["verdict"].startswith("DEV FAIL") and "do not run the hold-out" in r["verdict"]
    g4 = next(g for g in r["gates"] if g["gate"] == 4)
    assert g4["status"] == "PASS" and "80 trades" in g4["detail"]          # 80 >= 70 dev minimum


def test_dev_below_70_trades_is_inconclusive_even_if_gates_fail(tmp_path):
    assert gt.MIN_DEV_TRADES == 70
    few = fake_run(tmp_path, "d", "dev", 69, -1.0, 0.9, -3.0, -1.0, [(2016, 69, -69)])
    assert gt.evaluate(few)["verdict"].startswith("DEV INCONCLUSIVE")      # gate 4 is checked first
    ok = fake_run(tmp_path, "d70", "dev", 70, 3.0, 1.5, 0.5, 2.5, [(2016, 70, 210)])
    assert gt.evaluate(ok)["verdict"].startswith("DEV GATES 1–3 PASS")


def test_dev_pass_allows_holdout(tmp_path):
    dev = fake_run(tmp_path, "d", "dev", 80, 3.0, 1.5, 0.5, 2.5, [(2016, 40, 120), (2017, 40, 120)])
    assert gt.evaluate(dev)["verdict"].startswith("DEV GATES 1–3 PASS")


def test_final_pass_inconclusive_and_fail(tmp_path):
    yrs = [(y, 20, 60.0) for y in range(2015, 2022)]
    dev = fake_run(tmp_path, "d", "dev", 140, 3.0, 1.5, 0.5, 2.5, yrs, pnl=good_pnl(140))
    ho = fake_run(tmp_path, "h", "holdout", 40, 3.0, 1.5, 0.5, 2.5, [(2022, 20, 60.0), (2023, 20, 60.0)], pnl=good_pnl(40))
    r = gt.evaluate(dev, ho)
    assert r["verdict"].startswith("PASS on gates 1–6") and "gate 7" in r["verdict"]
    few = fake_run(tmp_path, "h2", "holdout", 12, 3.0, 1.5, 0.5, 2.5, [(2022, 12, 36.0)], pnl=good_pnl(12))
    assert gt.evaluate(dev, few)["verdict"].startswith("INCONCLUSIVE — too few trades")
    thin_dev = fake_run(tmp_path, "d3", "dev", 60, 3.0, 1.5, 0.5, 2.5, yrs, pnl=good_pnl(60))
    big_ho = fake_run(tmp_path, "h4", "holdout", 60, 3.0, 1.5, 0.5, 2.5, [(2022, 60, 180.0)], pnl=good_pnl(60))
    assert gt.evaluate(thin_dev, big_ho)["verdict"].startswith("INCONCLUSIVE — too few trades")   # 120 total, dev < 70
    bad = fake_run(tmp_path, "h3", "holdout", 40, -1.0, 0.8, -2.0, -1.0, [(2022, 40, -40.0)], pnl=[-1.0] * 40)
    assert gt.evaluate(dev, bad)["verdict"].startswith("FAIL")
    lumpy = fake_run(tmp_path, "d2", "dev", 140, 3.0, 1.5, 0.5, 2.5, [(2015, 140, 420.0)] + [(y, 1, -1.0) for y in range(2016, 2022)],
                     pnl=good_pnl(140))
    assert gt.evaluate(lumpy, ho)["verdict"].startswith("INCONCLUSIVE — gate 5 or 6")


def test_dirty_runs_and_wrong_periods_are_flagged(tmp_path):
    dev = fake_run(tmp_path, "d", "dev", 140, 3.0, 1.5, 0.5, 2.5, [(2016, 140, 420.0)], dirty=True)
    assert "NOT VALID AS PROOF" not in gt.evaluate(dev)["verdict"]          # dev-only: note, not verdict
    assert any("uncommitted" in n for n in gt.evaluate(dev)["notes"])
    with pytest.raises(gt.RunError):
        gt.evaluate(dev, dev)                                                 # a dev run passed as hold-out


def test_gate_thresholds_match_blueprint():
    text = (ROOT / "playbook" / "Backtest_Blueprint.md").read_text()
    for needle in (f"≥ {gt.MIN_TOTAL_TRADES} in total", f"≥ {gt.MIN_HOLDOUT_TRADES} in the hold-out",
                   f"≥ {gt.MIN_DEV_TRADES} in dev", f"fewer than {gt.MIN_DEV_TRADES} dev trades",
                   f"profit factor ≥ {gt.MIN_PROFIT_FACTOR}", f"> {gt.MAX_YEAR_SHARE:.0%} of net profit",
                   f"≥ {gt.MIN_NONNEG_YEAR_SHARE:.0%} of years",
                   f"< ${CFG.starting_capital * gt.MAX_MC_P95_DD_FRACTION:g}"):
        assert needle in text, needle


def test_gates_on_a_real_engine_run(tmp_path):
    rows = []
    for d in pd.bdate_range("2016-01-04", periods=5):
        for t in range(570, 585):
            rows.append((f"{d:%Y-%m-%d}T{t // 60:02d}:{t % 60:02d}:00-05:00", 2001, 2002, 2000, 2001))
        for t in range(585, 725):
            rows.append((f"{d:%Y-%m-%d}T{t // 60:02d}:{t % 60:02d}:00-05:00", 2002.25, 2002.25, 2002.25, 2002.25))
    p = tmp_path / "bars.csv"
    pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"]).to_csv(p, index=False)
    out = ob.main(["--period", "dev", "--data", str(p), "--out", str(tmp_path / "res")])
    r = gt.evaluate(out)
    assert r["mode"] == "dev" and {g["gate"] for g in r["gates"]} == {1, 2, 3, 4, 7}
    assert gt.list_runs(tmp_path / "res")[0]["period"] == "dev"


def test_dev_minimum_is_recorded_in_decisions_and_registry():
    d5 = (ROOT / "toolbox" / "pre_run_decisions.md").read_text()
    notes = (ROOT / "toolbox" / "backtest_notes.md").read_text()
    assert f"≥ {gt.MIN_DEV_TRADES} trades in dev" in d5
    assert f"≥ {gt.MIN_DEV_TRADES} dev, ≥ {gt.MIN_HOLDOUT_TRADES} hold-out and ≥ {gt.MIN_TOTAL_TRADES} total" in notes


# ------------------------------------------------------------------ session log
TRADE = {"date": "2026-11-17", "or_high": "5012.25", "or_low": "5010", "traded": True, "direction": "long",
         "signal_bar_close": "09:55", "entry_time": "09:55", "entry_price": "5012.75", "exit_time": "10:40",
         "exit_price": "5017", "exit_reason": "target", "chart_screenshot_path": "logs/charts/x.png"}


def test_log_row_derives_numbers_from_config():
    r = st.build_log_row(TRADE, CFG)
    row = r["row"]
    assert (row["stop_price"], row["entry_limit_price"], row["target_price"]) == ("5010", "5014.25", "5017")
    assert row["risk_usd"] == "17.00" and row["net_usd"] == "19.25" and row["rule_violation"] == "N"
    assert row["equity_usd"] == "519.25" and not r["violations"]


def test_log_flags_rule_breaks_and_forces_violation():
    bad = {**TRADE, "signal_bar_close": "10:03", "entry_time": "10:07", "entry_price": "5014.5",
           "exit_time": "12:10", "exit_price": "5015", "exit_reason": "time_exit"}
    r = st.build_log_row(bad, CFG)
    assert r["row"]["rule_violation"] == "Y" and len(r["violations"]) == 4
    assert "auto-flag" in r["row"]["notes"]
    wide = {**TRADE, "or_high": "5015", "entry_price": "5015.25", "exit_price": "5016"}
    assert any("pre-check" in v for v in st.build_log_row(wide, CFG)["violations"])


def test_log_rejects_bad_input():
    for bad in ({**TRADE, "entry_price": "5012.6"}, {**TRADE, "mode": "LIVE"}, {**TRADE, "date": "2999-01-01"},
                {**TRADE, "direction": "short"}, {**TRADE, "exit_time": "09:50"},
                {"date": "2026-11-17", "traded": False, "no_trade_reason": "contract_roll_in_session"},
                {"date": "2026-11-17", "traded": False, "no_trade_reason": "made_up"},
                {**TRADE, "notes": "two\nlines"}):
        with pytest.raises(st.LogError):
            st.build_log_row(bad, CFG)


def test_no_trade_rows_and_precheck_mismatch():
    r = st.build_log_row({"date": "2026-11-16", "traded": False, "no_trade_reason": "not_ready"}, CFG)
    assert r["row"]["or_high"] == "" and r["row"]["net_usd"] == "0"
    r = st.build_log_row({"date": "2026-11-16", "or_high": "5015", "or_low": "5005", "traded": False,
                          "no_trade_reason": "no_breakout"}, CFG)
    assert any("or_too_wide_for_risk_cap" in c for c in r["checks"])


def test_append_then_status_round_trip(tmp_path):
    p = tmp_path / "logs" / "trade_log.csv"
    st.append_log_row(p, st.build_log_row(TRADE, CFG)["row"])
    log = st.read_log(p)
    with pytest.raises(st.LogError, match="already in the log"):
        st.build_log_row(TRADE, CFG, log)
    nxt = st.build_log_row({"date": "2026-11-18", "or_high": "5020", "or_low": "5005", "traded": False,
                            "no_trade_reason": "or_too_wide_for_risk_cap"}, CFG, log)
    st.append_log_row(p, nxt["row"])
    s = st.status(st.read_log(p), CFG)
    assert s["verdict"] == "GO" and s["sessions"] == 2 and s["equity_usd"] == 519.25
    assert list(st.read_log(p).columns) == st.template_columns()
