# Pre-Run Decisions (fill in BEFORE the first real-data run)

Each decision changes what the backtest measures. Make it once, write the choice and the
date, put the value into the place named in **Lands in**, run the tests, commit, and tag
`v1.1-frozen`. Changing a decision after you have seen real results = a new version (playbook §5).

| # | Decision | Options and trade-offs | Suggested | Lands in | Your choice / date |
|---|---|---|---|---|---|
| D1 | **Risk cap** | **Keep $25:** honest for $500, but few trades, quiet mornings only, likely INCONCLUSIVE. **Raise to $50–75:** more trades, but one loss = 10–15% of $500. **Split:** pre-register a second config with a research cap to measure the edge in R, while paper trading only the $25 version. | Keep $25 for trading; optional pre-registered research config | `max_risk_usd`, then update the 4.25 / 3.75 / 0.75 numbers in the playbook and checklist (a test will tell you) | |
| D2 | **Time exit** | **11:30:** less exposure, more small time-exits. **12:00:** middle. **15:55:** more targets hit, but lunch chop and longer exposure. Pick one; never compare them on the same data. | 12:00 | `exit_time` | |
| D3 | **Entry order** | Decided in v1.1: risk-capped limit with attached stop. The market-then-bail alternative was removed (it was never run on real data). | — (decided) | playbook §2.1 | ✔ capped limit |
| D4 | **Costs** | Your broker's real all-in rate (commission + exchange + NFA) per side. | From your fee schedule | `commission_per_side_usd` | |
| D5 | **Minimum trades per period** | Higher = more reliable, more often INCONCLUSIVE. | ≥ 30 in the hold-out, ≥ 100 total | Blueprint §4 gate 4 + `backtest_notes.md` | |
| D6 | **Drawdown stop** | **Fixed $150:** protects capital, but likely fires on an ordinary 6-loss streak. **Higher, e.g. $200:** survives normal variance, costs more if the edge is gone. The pause level ($100) forces a review either way. | Decide now, in dollars, before you have seen any results | `drawdown_pause_usd`, `drawdown_stop_usd` | |
| D7 | **Paper-trading count** (playbook §6) | Count-based (≥ 15 trades) vs. time-based (e.g. 60 sessions). At a low trade rate, 15 trades can take a year. | Whichever comes later: 40 sessions and 15 trades | playbook §6 item 2 | |
| D8 | **Data** | Vendor, timezone, bar labelling (start/end), roll rule, `symbol` column, 09:20–09:29 pre-open bars, ≥ 13 months of history before 2015, MES-only cross-check for 2019–2021 (dev only). | Record before downloading | `backtest_notes.md` pre-registration + CLI flags | |
| D9 | **Period boundaries** | Dev 2015–2021 / hold-out 2022→. Moving the boundary after looking at any 2022+ data invalidates the hold-out. | Keep as is | `dev_start`, `dev_end`, `holdout_start` | |

Signed off (date + commit hash of the `v1.1-frozen` tag): ____________________
