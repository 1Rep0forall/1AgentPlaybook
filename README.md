# 1AgentPlaybook — MES Opening Range Breakout (v1)

A rules-based research and execution workflow for a 15-minute Opening Range
Breakout on Micro E-mini S&P 500 futures (MES), built on the
**One Agent + Playbook + Toolbox + Proof** approach.

> **SIM / paper only.** This is a research framework, not financial advice.
> Futures are leveraged; losses can exceed planned stops.

| Part | Path |
|---|---|
| Playbook (rules, no-trade, escalation, proof) | `playbook/MES_ORB_Playbook_v1.md` |
| 10-year backtest blueprint | `playbook/Backtest_Blueprint.md` |
| Frozen rules (single source of truth) | `toolbox/backtest_config.json` |
| Trade log template | `toolbox/trade_log_template.csv` |
| Session checklist | `toolbox/session_checklist.md` |
| Run registry / notes | `toolbox/backtest_notes.md` |
| Backtest engine | `backtest/orb_backtest.py` |
| Synthetic smoke-test data generator | `backtest/make_synthetic_data.py` |

## Quick start
```bash
pip install pandas numpy            # matplotlib optional (equity chart)
# 1) smoke test the pipeline (results are meaningless)
python backtest/make_synthetic_data.py --out data/synthetic_1min.csv
python backtest/orb_backtest.py --data data/synthetic_1min.csv --config toolbox/backtest_config.json
# 2) real data (example: naive Central-Time 1-min ES bars stamped at bar start)
python backtest/orb_backtest.py --data data/es_1min.csv --data-tz America/Chicago \
    --bar-label start --config toolbox/backtest_config.json --out results
```
See the docstring at the top of `backtest/orb_backtest.py` for the input CSV format.

## The biggest assumption to review first
The stop sits at the opposite side of the opening range, so dollar risk is at
least the full OR width. Under a $25 cap (after $2 fees and 2 ticks of slippage), only
opening ranges of about **4 points or less** qualify. At current index levels
most sessions will be no-trade days. Check `or_width_pts` in `day_log.csv`
before you interpret any result. See Playbook §0 for the full assumption list.
