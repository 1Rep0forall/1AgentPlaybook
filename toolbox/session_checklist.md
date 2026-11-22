# Session Checklist — MES ORB v1.1
Copy to `logs/sessions/YYYY-MM-DD.md` each session. All times ET. Numbers below come from
`python backtest/session_tools.py rules` (re-run it if the config ever changes).

**Date:** ____  **Mode:** SIM / LIVE  **Playbook version:** v1.1  **Equity at start:** $____

## Pre-session (09:00–09:29)
- [ ] `python backtest/session_tools.py status --log logs/trade_log_<year>.csv` → verdict **GO** (else `not_ready`, log, done)
- [ ] Not a holiday or early-close day (CME calendar checked); scheduled events noted: ____
- [ ] Platform connected; correct front-month contract (check roll week)
- [ ] Platform risk limits on: max position 1 contract, daily loss limit $50 (if supported)
- [ ] Equity ≥ broker minimum + $100; no open positions
- [ ] I am rested, undistracted and available until 12:00 ET (else `not_ready`)

## Opening range (09:30–09:45)
- [ ] OR High ____  OR Low ____  Width ____ pts
- [ ] Est. risk = (width + 0.75) × 5 + 2 = $____ → ≤ $25? **Y / N** (N → `or_too_wide_for_risk_cap`, log, done)
- [ ] Width ≥ 1.0 pt? **Y / N** (N → `or_too_narrow`, log, done)
- [ ] Limit prices: LONG = OR Low + 4.25 = ____   SHORT = OR High − 4.25 = ____

## Entry window (09:45–11:00)
- [ ] Signal: 5-min bar closing at ____ (time), LONG / SHORT, close ____ (none by 11:00 → `no_breakout`)
- [ ] One order sent: limit entry at ____ **with stop attached** at ____ (opposite range boundary)
- [ ] Filled at ____ within the next 1-min bar? (else cancel → `entry_limit_not_filled`, done)
- [ ] Target set: fill ± 1.5 × |fill − stop| = ____ (rounded away from the fill)

## Management
- [ ] No stop/target moves, no adds, no second trade
- [ ] Flat by 12:00 ET

## Close-out (Proof — playbook §4.1)
- [ ] Log row complete (reason code from playbook §2.2, or full trade details incl. net $ and R)
- [ ] Chart screenshot saved to `logs/charts/YYYY-MM-DD.png`
- [ ] 1-min bars for today exported; `session_tools.py reconcile` run → verdict ____ , model net $____
- [ ] Any mismatch explained in `notes` (rule violation / data / paper fill / rule ambiguity)
- [ ] Rule violation? **N / Y** → if Y, escalation applied
- [ ] `status` re-run → tomorrow's verdict ____ ; files committed
