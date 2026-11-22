# Session Checklist — MES ORB v1.0
Copy to `logs/sessions/YYYY-MM-DD.md` each session. All times ET.

**Date:** ____  **Mode:** SIM / LIVE  **Playbook version:** v1.0  **Account equity at start:** $____

## Pre-session (09:00–09:29)
- [ ] Not a holiday or early-close day (check the CME calendar); scheduled events noted: ____
- [ ] Platform connected; data feed live; correct contract month (front month, check the roll week)
- [ ] Account equity ≥ broker intraday margin + buffer; no open positions
- [ ] Bracket/OCO template set: 1 contract, stop and target as working orders
- [ ] Yesterday's log row complete; account drawdown below the $100 pause level
- [ ] I am rested, undistracted and available until 12:00 ET (else `not_ready`)

## Opening range (09:30–09:45)
- [ ] OR High ____  OR Low ____  Width ____ pts
- [ ] Est. risk = (width + 0.50) × 5 + 2 = $____ → ≤ $25? **Y / N** (N → NO-TRADE, log, done)
- [ ] Width ≥ 1.0 pt? **Y / N**

## Entry window (09:45–11:00)
- [ ] Signal: 5-min bar closing ____ (time), direction LONG / SHORT, close ____
- [ ] Fill ____ → actual risk $____ ≤ $25? (else exit, `risk_cap_at_entry`)
- [ ] Stop ____ (opposite OR boundary)  Target ____ (1.5R, rounded away from entry)
- [ ] Bracket confirmed working at the broker (screenshot of order ticket)

## Management
- [ ] No stop moves, no adds, no second trade
- [ ] Flat by 12:00 ET (time exit at market if needed)

## Close-out (Proof)
- [ ] Trade-log row written (incl. no-trade reason or exit details, net $, R)
- [ ] Chart screenshot saved to `logs/charts/YYYY-MM-DD.png`
- [ ] Rule violation? **N / Y** → if Y, escalation applied: ____
- [ ] Day P&L $____  End equity $____
