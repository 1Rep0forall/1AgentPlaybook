# MES ORB Playbook — v1.1 (frozen)

> **One Agent + Playbook + Toolbox + Proof.** One agent (you, helped by an AI assistant following
> `CLAUDE.md`) runs the *same* playbook every session, uses the *same* tools, and calls nothing
> "done" without the *same* proof. Rules change only through §5.

**Status:** paper (SIM) only. No live capital until §6 is met.
**Frozen** at git tag `v1.1-frozen` (decisions in `toolbox/pre_run_decisions.md`). The $1.00/side
fee is a **placeholder** (D4); only the D4 rule in that file allows it to change within v1.1.
The data spec (D8) must be filled in before the first real-data run.
**Numbers come from config:** every number below is derived from `toolbox/backtest_config.json`.
Print today's values with `python backtest/session_tools.py rules`; a test fails if this page and
the config disagree.

---

## 0. What to expect (read once)

| # | Fact | What it means for you |
|---|---|---|
| A1 | The stop sits at the far side of the opening range, so risk ≥ the whole range. A $25 cap admits ranges of **≤ 3.75 points** only. | **Most days will be no-trade days**, more so as the index level rises. That is the rule working, not a problem to fix. |
| A2 | Fees + slippage are ≈ $4.50 a trade on a $13–25 risk. A full 1.5R target nets **≈ 1.0–1.2R** after costs. | You need to win **≈ 45–50%** of trades just to break even. |
| A3 | One full loss ≈ 5% of $500; six in a row ≈ 30%. Streaks of 6+ losses are *likely* over 100 trades even for a profitable strategy. | Drawdowns are normal. The escalation rules (§2.3) decide what you do, not your mood. |
| A4 | MES exists only since May 2019; the 10-year test uses ES prices as a stand-in. | Pre-2019 fills may be slightly optimistic. |
| A5 | Paper platforms usually fill limit orders on a *touch* and stops *without* slippage. The backtest does neither. | Paper P&L will look better than the backtest. Compare **decisions** with `reconcile`, not dollars. |
| A6 | News days are traded (and tagged). | Filtering them after seeing results would be curve-fitting. |

---

## 1. Session window (all times US/Eastern)

| Time (ET) | Phase | Action |
|---|---|---|
| 09:00–09:29 | Pre-session | Session checklist. `session_tools.py status` must say **GO**. |
| 09:30:00–09:44:59 | Opening range | Record **OR High** and **OR Low** from the 15 one-minute bars. No orders. |
| 09:45 | OR lock | `est. risk $ = (width + 0.75) × $5 + $2`. Over $25 → **no trade today**. Under 1.0 pt wide → no trade. Otherwise write down both limit prices: long `OR Low + 4.25`, short `OR High − 4.25`. |
| 09:45–11:00 | Entry window | **Trigger:** first completed **5-min bar** that closes **above OR High** (long) or **below OR Low** (short). Wicks don't count. |
| ≤ 11:00 | Last signal | The signal bar must *close* by 11:00. |
| 12:00 | Time exit | Flatten any open position at market. |

**One trade per session, maximum.** The first signal is the only signal. If it doesn't fill, the
session is over. You never wait for a "better" second breakout.

---

## 2. Execution

### 2.1 Steps

1. **09:45 — lock the range** and run the pre-check above. Log the result.
2. **Wait** for a 5-min close outside the range. Do not anticipate.
3. **Send one order: a limit entry at the risk-capped price with the stop attached** (a bracket/OCO
   or "attached stop" order, if your platform supports it). Long limit `OR Low + 4.25`, short limit
   `OR High − 4.25`; stop at the opposite range boundary. The limit makes a fill beyond the $25
   risk impossible; the attached stop means you are never in a position without protection.
4. **Not filled by the end of the next 1-minute bar → cancel.** Log `entry_limit_not_filled`. Done.
5. **Filled →** set the target at `fill ± 1.5 × |fill − stop|`, rounded to a tick *away* from the fill.
6. **Hands off.** No moving the stop or target, no adding, no early exit (except §2.3 incidents).
7. **12:00 →** flatten at market if still open.
8. **Close out** with the Proof in §4.1.

### 2.2 No-trade reason codes (log one every no-trade day)

The backtest emits the same codes in `day_log.csv`; a test keeps this table and the code in sync.

| Code | Condition | Where |
|---|---|---|
| `or_too_wide_for_risk_cap` | `(width + 0.75) × $5 + $2` over $25 at 09:45 | both |
| `or_too_narrow` | OR width under 1.0 point | both |
| `no_breakout` | No 5-min close outside the OR by 11:00 | both |
| `entry_limit_not_filled` | Price ran past the risk-capped limit before you were filled | both |
| `entry_beyond_stop` | Price was already back through the stop level when you could enter | both |
| `incomplete_or_data` | Data or platform problem during 09:30–09:45 | both |
| `incomplete_signal_data` | Data problem during the entry window before a breakout was seen | both |
| `no_data_in_entry_window` / `no_entry_bar` | No usable data/bar to act on in the entry window | both |
| `no_session_data` | Exchange holiday or missing data for a weekday | both |
| `contract_roll_in_session` | Data mixes two contract months within the session | backtest |
| `daily_loss_limit` | Day P&L ≤ −$50 (hard stop for the day) | live/paper only |
| `not_ready` | Checklist not fully green, or `status` was not GO | live/paper only |

**A no-trade day where you followed the rules is a successful day.** Long no-trade streaks are
expected (A1). They are never a reason to loosen a rule, take a "nearly valid" setup, or trade a
second signal. Compare your no-trade rate with the backtest's `trade_rate` instead.

### 2.3 Escalation — applied mechanically by `session_tools.py status`

| Trigger | Action |
|---|---|
| Platform, data or connection problem with a position open | Flatten immediately; note `incident` in `notes`; no more trades today. |
| Any rule violation (late entry, moved stop, second trade, oversized) | `rule_violation = Y`. Next 5 sessions are SIM only, even if you are live. |
| Drawdown ≥ `drawdown_pause_usd` ($100) | **PAUSE**: no trading until you have run `reconcile` on every losing trade and written a review in `backtest_notes.md`. Resume only if every loss was a valid playbook trade. |
| Drawdown ≥ `drawdown_stop_usd` ($150) | **STOP** v1. Back to research. Restarting needs a new version (§5) and new proof. |
| Any single loss > $50 | Investigate the fill. Check order type and broker settings before the next session. |
| Proof missing for an earlier session | **FIX-PROOF**: complete it before trading again. |

The $150 stop was fixed in advance (D6) knowing that it may trigger on an ordinary losing streak
(A3). If it does, v1 stops; you don't raise it after the fact.

### 2.4 Account and broker realism (small account)

- **Broker controls first:** if your platform supports them, set *max position size = 1 contract*
  and a *daily loss limit = $50*. A platform limit works even when you don't.
- **Margin:** confirm in writing your broker's minimum account balance, MES day-trade margin and
  auto-liquidation level. If equity falls below `minimum + $100`, trade SIM only.
- **Be flat by 12:00 ET.** The exit is long before the close, so overnight margin never applies, but
  know your broker's own intraday-margin cut-off and never rely on it.
- **Paper ≠ live fills (A5).** Record what the platform did; `reconcile` shows what the model
  expects. Keep a running count of decision mismatches caused by paper fills on touch.

---

## 3. Toolbox (built once, reused every session)

| Tool | File | Use |
|---|---|---|
| **Agent instructions** | `CLAUDE.md` | The one agent's operating loop and boundaries. |
| **Rules / config** | `toolbox/backtest_config.json` | Every rule number. Never edit a frozen version; copy it to a new version (§5). |
| **Session tools** | `backtest/session_tools.py` | `rules` (today's numbers), `status` (GO/PAUSE/STOP/FIX-PROOF), `reconcile` (your log vs. the model for one session). |
| **Trade log** | `toolbox/trade_log_template.csv` | Copy once to `logs/trade_log_<year>.csv`; one row per session, incl. no-trade days. Same column names as the backtest. |
| **Session checklist** | `toolbox/session_checklist.md` | Copy to `logs/sessions/YYYY-MM-DD.md` each day and tick it. |
| **Decisions** | `toolbox/pre_run_decisions.md` | D1–D9, made 2026-11-22 before any real data (D8 data spec still to fill). |
| **Run registry** | `toolbox/backtest_notes.md` | Every backtest run and every playbook change, with a reason. |
| **Backtest engine** | `backtest/orb_backtest.py` | Same script for every test; only config and data change. `--period dev|holdout` is required. |
| **Tests** | `tests/test_orb_backtest.py` | `python -m pytest tests/ -q` after any change. Includes checks that docs, template and config agree with the code. |

`logs/` and `results/` are **committed to git**: proof that only exists on one laptop is not proof.
Only raw market data (`data/`) is ignored.

---

## 4. Proof ("done" means all boxes ticked)

### 4.1 Session is DONE when…

- [ ] Log row complete: range, decision + reason code, and if traded: entry, stop, target, exit, exit reason, net $, R
- [ ] `rule_violation` filled in (Y/N); if Y, §2.3 applied
- [ ] Chart screenshot `logs/charts/YYYY-MM-DD.png` shows range lines, signal bar, entry, stop, target, exit
- [ ] `session_tools.py reconcile` run on the session's 1-min bars; verdict written to `reconcile_verdict`, model P&L to `model_net_usd`
- [ ] Any `DECISION_MISMATCH` explained in `notes` as one of: rule violation / data difference / paper-fill difference / rule ambiguity (→ §5)
- [ ] `session_tools.py status` re-run; verdict noted for tomorrow
- [ ] Log, checklist and screenshot committed

### 4.2 Backtest run is DONE when…

- [ ] Run made from a committed state (`git_worktree_dirty = false` in `run_manifest.json`); tests passed on that commit
- [ ] `run_id` and the key numbers recorded in `toolbox/backtest_notes.md`
- [ ] `summary.json → warnings` each resolved or accepted in writing
- [ ] Data checks: `open_alignment.months_flagged = 0`, `sessions_with_multiple_symbols` explained, `weekdays_without_data` ≈ holidays
- [ ] 10 random trades and 5 no-trade days checked by hand against a chart
- [ ] Verdict written against Blueprint §4 gates: **PASS / FAIL / INCONCLUSIVE**
- [ ] Results folder committed

---

## 5. Changing the playbook (how edge cases become fixes, not chat patches)

Every discovered edge case is classified **before** anything is edited:

| Type | Example | What you do |
|---|---|---|
| **Clarification** — no trade decision or price changes | Wording is ambiguous, a checklist step is missing | Edit the docs, add a test if possible, note it in `backtest_notes.md`. Version stays v1.1. |
| **Bug fix** — the code doesn't do what the written rule says | Backtest fills a limit the rule says can't fill | Write a failing test first, fix the code, re-run **dev only**. Blueprint §2.3 says when the hold-out may be re-run. |
| **Rule change** — any trade decision or price would change | New exit time, different cap, a filter | New version (v1.2): new config, dev re-run, **forward paper validation from zero**. Not while PAUSE/STOP is active. At most one rule change per calendar month. |

Never change a rule in the middle of a session, a losing streak, or a conversation about a single trade.

## 6. Paper → live (all required)

1. Backtest verdict **PASS** (Blueprint §4). **INCONCLUSIVE or FAIL never goes live**: keep paper trading as data collection, or stop.
2. **Both** ≥ 40 paper sessions and ≥ 15 paper trades (D7), with **zero** rule violations.
3. `reconcile`: no unexplained `DECISION_MISMATCH`; entry/exit price differences average ≤ 1 tick against the model.
4. Broker margin, minimum balance, auto-liquidation level and fees confirmed in writing; platform risk limits set (§2.4).
5. Written: *"A normal losing streak can cost $___ (backtest Monte Carlo p95 drawdown). I will not change rules mid-streak."*

> **Risk disclosure:** futures are leveraged. Losses can exceed a planned stop (gaps, outages,
> fast markets). Past and simulated results do not guarantee future results. This is a research
> and process framework, not financial advice.
