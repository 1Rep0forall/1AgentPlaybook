# Backtest Notes & Run Registry

Add one entry per run, newest at the bottom. **Every** run counts, including
failed and abandoned ones. The number of variants tried is your
curve-fitting budget: the more you try, the more skeptical you must be of
the best one.

## Pre-registration (fill in BEFORE the first real-data run)
- Playbook / config version: v1.0 (`toolbox/backtest_config.json`)
- Data: vendor ____, symbol(s) ____ (ES proxy pre-2019-05, MES after?), bar size ____, timezone ____, labelling start/end ____
- Roll rule: ____ (e.g. roll when next contract's daily volume > front; unadjusted prices)
- Development (in-sample) period: 2015-01-01 → 2021-12-31
- Hold-out (out-of-sample) period: 2022-01-01 → latest — **run once, after rules are frozen**
- Pass/fail gates: see `playbook/Backtest_Blueprint.md` §4 (copy exact thresholds here)
- Variant budget: max 3 pre-declared variants (e.g. 2R target) — reported, never cherry-picked

## Run log

| # | Date | run_id | Config ver. | Period | Purpose / hypothesis | Key result (net $, PF, exp R, maxDD, trades) | Decision |
|---|---|---|---|---|---|---|---|
| 0 | 2026-11-22 | (synthetic) | v1.0 | 2023–2024 synthetic | Pipeline smoke test only | 0 trades: every synthetic OR wider than the $25 cap allows; meaningless for edge | Pipeline OK |
| 1 | | | | | | | |
