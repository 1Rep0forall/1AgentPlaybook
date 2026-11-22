# 1AgentPlaybook — MES Opening Range Breakout (v1.1, frozen at tag `v1.1-frozen`)

A rules-based research and paper-trading workflow for a 15-minute Opening Range
Breakout on Micro E-mini S&P 500 futures (MES), built on the
**One Agent + Playbook + Toolbox + Proof** approach.

> **Paper trading only.** Nothing here has been tested on real market data yet: the only runs so
> far used synthetic random-walk data to check the code. This is a research framework, not
> financial advice. Futures are leveraged; losses can exceed planned stops.

| Part | Path |
|---|---|
| Agent instructions (daily loop, boundaries) | `CLAUDE.md` |
| Playbook (rules, no-trade codes, escalation, proof, change process) | `playbook/MES_ORB_Playbook_v1.md` |
| 10-year backtest blueprint (method, hold-out governance, gates) | `playbook/Backtest_Blueprint.md` |
| Rules config (frozen at `v1.1-frozen`; fee is a $1.00/side placeholder, D4) | `toolbox/backtest_config.json` |
| Pre-run decisions D1–D9 | `toolbox/pre_run_decisions.md` |
| Trade log template / session checklist | `toolbox/trade_log_template.csv`, `toolbox/session_checklist.md` |
| Run registry, change log, pause reviews | `toolbox/backtest_notes.md` |
| Backtest engine | `backtest/orb_backtest.py` |
| Session tools: `rules`, `status`, `reconcile` | `backtest/session_tools.py` |
| Synthetic smoke-test data | `backtest/make_synthetic_data.py` |
| Tests (logic + docs/config consistency) | `tests/test_orb_backtest.py` |

## Quick start
```bash
pip install pandas numpy pytest     # matplotlib optional (equity chart)
python -m pytest tests/ -q
python backtest/session_tools.py rules
```
Smoke test (synthetic data, 2016–2017, inside the dev period; results mean nothing):
```bash
python backtest/make_synthetic_data.py --daily-vol 0.0012 --out data/synthetic_quiet.csv
python backtest/orb_backtest.py --period dev --data data/synthetic_quiet.csv --out /tmp/orb_smoke
```
Real data (fill in the D8 data spec first; example: naive Central-Time 1-min ES bars stamped at bar start):
```bash
python backtest/orb_backtest.py --period dev --data data/es_1min.csv --data-tz America/Chicago --bar-label start --config toolbox/backtest_config.json
```
`--period holdout` is a **one-time** run (Blueprint §2.3) and is refused on an uncommitted worktree.
See the docstring at the top of `backtest/orb_backtest.py` for the input CSV format.

## What git tracks
`logs/` (paper-trading proof) and `results/` (backtest outputs) are committed; that is the proof.
Only raw market data in `data/` is ignored.

## The biggest thing to understand first
The stop sits at the opposite side of the opening range, so dollar risk is at least the full
range width. Under a $25 cap (after $2 fees and 3 ticks of breakout + slippage), only opening
ranges of about **3.75 points or less** qualify. Most sessions will be no-trade days, and a low
trade count may make the 10-year result **inconclusive**. That is an honest outcome, not a bug.
