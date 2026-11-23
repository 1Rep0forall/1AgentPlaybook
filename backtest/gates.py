#!/usr/bin/env python3
"""
Evaluate backtest runs against the pre-registered Blueprint §4 gates.

    python backtest/gates.py --dev results/<dev_run_id>                    # dev check (gates 1-3)
    python backtest/gates.py --dev results/<dev_run> --holdout results/<holdout_run>   # final verdict

The thresholds below are the ones written in playbook/Backtest_Blueprint.md §4;
a test fails if the two ever disagree. This script only READS result folders.
Gate 7 (hand-check of trades against charts) can't be automated: it is always
reported as MANUAL and the verdict says so.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orb_backtest as ob  # noqa: E402

# Blueprint §4 (D5 + pre-registered gates). Change only with a new version (playbook §5).
MIN_TOTAL_TRADES = 100
MIN_HOLDOUT_TRADES = 30
MIN_PROFIT_FACTOR = 1.15
MAX_YEAR_SHARE = 0.50          # no single year > 50% of net profit
MIN_NONNEG_YEAR_SHARE = 0.60   # >= 60% of years with trades non-negative
MAX_MC_P95_DD_FRACTION = 0.50  # Monte Carlo p95 max drawdown < 50% of starting capital ($250 on $500)


class RunError(ValueError):
    pass


def load_run(path) -> dict:
    p = Path(path)
    need = ("summary.json", "run_manifest.json", "by_year.csv", "trade_log.csv")
    missing = [f for f in need if not (p / f).exists()]
    if missing:
        raise RunError(f"{p} is not a complete backtest result folder (missing {missing})")
    run = {"path": str(p),
           "summary": json.loads((p / "summary.json").read_text()),
           "manifest": json.loads((p / "run_manifest.json").read_text()),
           "by_year": pd.read_csv(p / "by_year.csv"),
           "trades": pd.read_csv(p / "trade_log.csv"),
           "sensitivity": pd.read_csv(p / "sensitivity.csv") if (p / "sensitivity.csv").exists() else None}
    run["period"] = run["manifest"].get("period", "unknown")
    return run


def gate(num, name, status, detail):
    return {"gate": num, "name": name, "status": status, "detail": detail}


def period_gates(run: dict) -> list:
    """Gates 1-3 for one period (dev or hold-out)."""
    s, label = run["summary"], run["period"]
    n, exp, pf = s.get("trades", 0), s.get("expectancy_usd"), s.get("profit_factor")
    out = []
    if not n:
        out.append(gate(1, f"{label}: positive expectancy and PF ≥ {MIN_PROFIT_FACTOR}", "FAIL", "no trades"))
    else:
        pf_ok = pf is None or pf >= MIN_PROFIT_FACTOR      # PF is None only when there were no losing trades
        ok = exp is not None and exp > 0 and pf_ok
        out.append(gate(1, f"{label}: positive expectancy and PF ≥ {MIN_PROFIT_FACTOR}", "PASS" if ok else "FAIL",
                        f"expectancy ${exp} per trade, profit factor {pf if pf is not None else 'n/a (no losses)'}"))
    sens = run["sensitivity"]
    if sens is None:
        out.append(gate(2, f"{label}: costs ×2 expectancy ≥ 0", "MISSING", "run had no sensitivity.csv"))
        out.append(gate(3, f"{label}: same sign under target-first", "MISSING", "run had no sensitivity.csv"))
        return out
    rows = sens.set_index("scenario")

    def exp_of(name):
        if name not in rows.index or pd.isna(rows.loc[name, "expectancy_usd"]):
            return None
        return float(rows.loc[name, "expectancy_usd"])

    x2 = exp_of("costs_x2_same_trades")
    out.append(gate(2, f"{label}: costs ×2 expectancy ≥ 0",
                    "MISSING" if x2 is None else ("PASS" if x2 >= 0 else "FAIL"),
                    "no trades to re-price" if x2 is None else f"expectancy at 2× costs ${x2}"))
    other = next((i for i in rows.index if i.startswith("ambiguous_")), None)
    alt = exp_of(other) if other else None
    if exp is None or alt is None:
        out.append(gate(3, f"{label}: same sign under target-first", "MISSING", "no trades or no ambiguity row"))
    else:
        same = np.sign(exp) == np.sign(alt)
        out.append(gate(3, f"{label}: same sign under {other.replace('ambiguous_', '')}",
                        "PASS" if same else "FAIL", f"base ${exp} vs ${alt}"))
    return out


def proof_problems(run: dict) -> list:
    m = run["manifest"]
    probs = []
    if m.get("git_worktree_dirty") is not False:
        probs.append(f"{run['period']} run was made from an uncommitted state: it doesn't count as proof "
                     "(playbook §4.2). Commit and re-run.")
    for w in run["summary"].get("warnings", []):
        probs.append(f"{run['period']} warning to resolve or accept in backtest_notes.md: {w}")
    return probs


def evaluate(dev_path, holdout_path=None) -> dict:
    dev = load_run(dev_path)
    if dev["period"] != "dev":
        raise RunError(f"--dev must point to a dev run (this one is '{dev['period']}')")
    runs = [dev]
    if holdout_path:
        ho = load_run(holdout_path)
        if ho["period"] != "holdout":
            raise RunError(f"--holdout must point to a hold-out run (this one is '{ho['period']}')")
        if ho["manifest"]["config"] != dev["manifest"]["config"]:
            raise RunError("dev and hold-out runs used different configs; they can't be judged together")
        runs.append(ho)

    gates = [g for r in runs for g in period_gates(r)]
    notes = [p for r in runs for p in proof_problems(r)]
    gates.append(gate(7, "hand-check of 10 trades and 5 no-trade days", "MANUAL",
                      "confirm in backtest_notes.md (playbook §4.2)"))
    core_fail = any(g["status"] == "FAIL" for g in gates if g["gate"] in (1, 2, 3))
    core_missing = any(g["status"] == "MISSING" for g in gates if g["gate"] in (1, 2, 3))

    if not holdout_path:
        n = dev["summary"].get("trades", 0)
        notes.append("The Blueprint sets trade minimums for the hold-out (≥ 30) and the total (≥ 100) but not "
                     f"for dev alone. Dev has {n} trades; whether that is 'too few' is your call to record.")
        if core_fail:
            verdict = "DEV FAIL — stop; do not run the hold-out (Blueprint §2.3)"
        elif core_missing:
            verdict = "DEV INCOMPLETE — re-run with sensitivity before judging"
        else:
            verdict = "DEV GATES 1–3 PASS — hold-out may be run once from a tagged, clean commit"
        return {"mode": "dev", "verdict": verdict, "gates": gates, "notes": notes, "runs": [r["path"] for r in runs]}

    ho = runs[1]
    total = dev["summary"].get("trades", 0) + ho["summary"].get("trades", 0)
    n_ho = ho["summary"].get("trades", 0)
    g4 = gate(4, f"enough trades: ≥ {MIN_TOTAL_TRADES} total and ≥ {MIN_HOLDOUT_TRADES} in the hold-out",
              "PASS" if total >= MIN_TOTAL_TRADES and n_ho >= MIN_HOLDOUT_TRADES else "FAIL",
              f"{total} total, {n_ho} in the hold-out")

    years = pd.concat([dev["by_year"], ho["by_year"]])
    years = years[years["trades"] > 0]
    net = float(years["net_pnl_usd"].sum())
    if years.empty or net <= 0:
        g5 = gate(5, "profit spread across years", "FAIL", f"no net profit across the full period (${net:.2f})")
    else:
        top = float(years["net_pnl_usd"].max()) / net
        nonneg = float((years["net_pnl_usd"] >= 0).mean())
        ok = top <= MAX_YEAR_SHARE and nonneg >= MIN_NONNEG_YEAR_SHARE
        g5 = gate(5, "profit spread across years", "PASS" if ok else "FAIL",
                  f"largest year = {top:.0%} of net profit; {nonneg:.0%} of traded years non-negative")

    cfg = ob.Config(**{k: v for k, v in dev["manifest"]["config"].items() if k in ob.Config.__dataclass_fields__})
    pnl = pd.concat([dev["trades"]["net_usd"], ho["trades"]["net_usd"]]).to_numpy(dtype=float)
    mc = ob.monte_carlo(pnl, cfg)
    limit = cfg.starting_capital * MAX_MC_P95_DD_FRACTION
    if "max_dd_usd_p95" not in mc:
        g6 = gate(6, f"Monte Carlo p95 max drawdown < ${limit:g}", "SKIPPED", mc.get("note", "skipped"))
    else:
        g6 = gate(6, f"Monte Carlo p95 max drawdown < ${limit:g}",
                  "PASS" if mc["max_dd_usd_p95"] < limit else "FAIL", f"p95 max drawdown ${mc['max_dd_usd_p95']}")
    gates = [g4] + gates[:-1] + [g5, g6, gates[-1]]

    if g4["status"] != "PASS":
        verdict = "INCONCLUSIVE — too few trades (gate 4)"
    elif core_fail:
        verdict = "FAIL — gate 1, 2 or 3 failed with enough trades"
    elif core_missing:
        verdict = "INCOMPLETE — a run is missing sensitivity results"
    elif g6["status"] != "PASS" or g5["status"] != "PASS":
        verdict = "INCONCLUSIVE — gate 5 or 6 not met"
    else:
        verdict = "PASS on gates 1–6 — gate 7 (hand-check) still required before it counts"
    if any("uncommitted" in n for n in notes):
        verdict += " (NOT VALID AS PROOF: a run was made from uncommitted changes)"
    return {"mode": "final", "verdict": verdict, "gates": gates, "notes": notes,
            "runs": [r["path"] for r in runs]}


def list_runs(results_dir) -> list:
    """Newest first: one line of key numbers per result folder (for the dashboard)."""
    out = []
    root = Path(results_dir)
    if not root.exists():
        return out
    for p in sorted((d for d in root.iterdir() if d.is_dir()), reverse=True):
        try:
            r = load_run(p)
        except (RunError, ValueError, OSError):
            continue
        s, m = r["summary"], r["manifest"]
        out.append({"run_id": p.name, "path": str(p), "period": r["period"], "created_utc": m.get("created_utc"),
                    "trades": s.get("trades"), "net_pnl_usd": s.get("net_pnl_usd"),
                    "profit_factor": s.get("profit_factor"), "expectancy_r": s.get("expectancy_r"),
                    "max_drawdown_usd": s.get("max_drawdown_usd"),
                    "clean_commit": m.get("git_worktree_dirty") is False,
                    "warnings": len(s.get("warnings", []))})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate runs against Blueprint §4 gates")
    ap.add_argument("--dev", required=True, help="dev result folder")
    ap.add_argument("--holdout", help="hold-out result folder (for the final verdict)")
    a = ap.parse_args(argv)
    try:
        r = evaluate(a.dev, a.holdout)
    except RunError as e:
        sys.exit(f"Error: {e}")
    for g in r["gates"]:
        print(f"  gate {g['gate']}  {g['status']:8s} {g['name']}: {g['detail']}")
    for n in r["notes"]:
        print(f"  - {n}")
    print(f"\nVERDICT: {r['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
