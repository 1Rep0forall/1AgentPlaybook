# Pre-Run Decisions (fill in BEFORE the first real-data run)

Each decision changes what the backtest measures. Make it once, write the
choice and the date here, commit, then run. Changing a decision after you
have seen real results = a new version (v1.2) and a spent hold-out.

| # | Decision | Options and trade-offs | Suggested | Your choice / date |
|---|---|---|---|---|
| D1 | **Risk cap** (`max_risk_usd`) | **Keep $25:** honest for a $500 account, but few trades, mostly quiet mornings, likely "inconclusive". **Raise to $50–75:** more trades and better statistics, but one loss = 10–15% of $500. **Change the stop rule** (e.g. range midpoint): a different strategy, tighter stops, more cost drag. **Split research from trading:** run once with a research cap to measure the edge in R, and report the subset of days that fit $25. | Split (pre-register both runs) | |
| D2 | **Time exit** (`exit_time`) | **11:30:** less exposure, more small time-exits. **12:00:** middle. **15:55:** more targets hit, but lunch chop and longer exposure. Pick one; never compare them on the same data. | 12:00 | |
| D3 | **Entry order** (`entry_mode`) | **capped_limit:** never fills beyond the cap; occasionally misses a trade. **market_bail:** fills more often but pays ~$4.50 to bail out when the fill is too far away. | capped_limit (v1.1 default) | |
| D4 | **Costs** (`commission_per_side_usd`) | Use your broker's real all-in rate (commission + exchange + NFA) per side. | From your fee schedule | |
| D5 | **Minimum trades per period** | Higher (e.g. ≥30 in the hold-out) = more reliable, more often "inconclusive". | Add a per-period minimum; accept "inconclusive" | |
| D6 | **Drawdown stop** (playbook §2.2) | **Fixed $150:** protects capital, but a 55–60%-loss strategy has a 73–88% chance of 6 straight losses in 100 trades, so it likely fires on noise. **Tied to backtest Monte Carlo `max_dd_usd_p95`:** respects normal variance, can allow bigger losses. | Keep $100 as a *pause*; stop beyond Monte Carlo p95 drawdown or p95 losing streak | |
| D7 | **Paper-trading gate** (playbook §6) | Count-based (≥15 trades) vs. time-based (e.g. 60 sessions). Depends on the real trade rate. | Decide after measuring the trade rate | |
| D8 | **Data** | Vendor, timezone, bar labelling (start/end), roll rule, `symbol` column, pre-open bars, ≥13 months of extra history, MES-only cross-check for 2019+. | Record before downloading | |
