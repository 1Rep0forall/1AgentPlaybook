# Backtest Notes & Run Registry

Add one entry per run, newest at the bottom. **Every** run counts, including
failed and abandoned ones. The number of variants tried is your
curve-fitting budget: the more you try, the more skeptical you must be of
the best one.

## Pre-registration (fill in BEFORE the first real-data run)
- Playbook / config version: v1.1 (`toolbox/backtest_config.json`)
- Data: vendor ____, symbol(s) ____ (ES proxy pre-2019-05, MES after?), bar size ____, timezone ____, labelling start/end ____, `symbol` column Y/N, pre-open bars Y/N, history starts ____ (≥13 months before test start)
- Decisions D1–D9: recorded in `toolbox/pre_run_decisions.md` (commit before the first run)
- Roll rule: ____ (e.g. roll when next contract's daily volume > front; unadjusted prices)
- Development (in-sample) period: 2015-01-01 → 2021-12-31
- Hold-out (out-of-sample) period: 2022-01-01 → latest — **run once, after rules are frozen**
- Pass/fail gates: see `playbook/Backtest_Blueprint.md` §4 (copy exact thresholds here)
- Variant budget: **one** trading config (v1.1) + optionally **one** pre-registered research config (D1). Nothing else.
- Frozen tag / commit: ____ (`git tag v1.1-frozen`)
- Hold-out used? **NO** ← change to the run_id when it happens. Never again after that (Blueprint §2.3).

## Run log

Rules: one row per run, written **before** you interpret the result. `Verdict` is PASS / FAIL /
INCONCLUSIVE against Blueprint §4, or "pipeline" for smoke tests.

| # | Date | run_id | Config ver. | Period | Purpose / hypothesis | Key result (net $, PF, exp R, maxDD, trades) | Verdict / decision |
|---|---|---|---|---|---|---|---|
| 0 | 2026-11-22 | (synthetic) | v1.0 | 2023–2024 synthetic | Pipeline smoke test only | 0 trades: every synthetic OR wider than the $25 cap allows; meaningless for edge | Pipeline OK |
| 0b | 2026-11-22 | (synthetic, quiet) | v1.1 | 2023–2024 synthetic `--daily-vol 0.0012` | Exercise trade logic after audit fixes | 274 trades, net −$790 after $1,192 costs on a random walk (expected: no edge + costs); meaningless for edge | Pipeline OK; 23 unit tests pass |
| 0c | 2026-11-22 | (synthetic, quiet, dev) | v1.1 draft | 2016–2017 synthetic `--daily-vol 0.0012`, `--period dev` | Smoke test after governance/tooling changes | 434 trades, net −$1,917 on a random walk (expected: no edge + costs); hold-out run refused on dirty tree as designed | pipeline; 30 tests pass |
| 1 | | | | | | | |

## Playbook change log (playbook §5)

| Date | Type (clarification / bug fix / rule change) | What and why | Version | Test added |
|---|---|---|---|---|
| 2026-11-22 | rule change (pre-run) | Capped-limit entry; full OR coverage; unified reason codes | v1.1 | yes |
| 2026-11-22 | clarification + tooling (pre-run) | Hold-out guard (`--period`), session_tools (rules/status/reconcile), removed 2R comparison, market_bail mode, mae/mfe columns; escalation numbers moved into config | v1.1 | yes |

## Pause reviews (playbook §2.3)

| Date | Drawdown | Losing trades reviewed (reconcile verdicts) | All valid playbook trades? | Resume? |
|---|---|---|---|---|
