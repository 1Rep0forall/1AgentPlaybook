# MES ORB Playbook — v1.1

> **One Agent + Playbook + Toolbox + Proof.** The agent (you, or an AI assistant)
> runs the *same* playbook every session, uses the *same* tools, and does not call
> anything "done" without the *same* proof. Rules change only by creating v1.1, v2, etc.

**Status:** SIM / paper only. No live capital until the graduation criteria (§6) are met.
**Rules source of truth:** `toolbox/backtest_config.json` (the backtest and live rules must match).

**v1.1 changes (2026-11-22, before any real-data run, so no hold-out has been used):** entry is now a
*risk-capped limit order* instead of "market, then exit if risk is too big" (the backtest could not
model the bail-out costs honestly); the 09:45 pre-check includes the one-tick breakout; all 15
opening-range bars are required; reason codes are unified between this playbook and the backtest.

---

## 0. Assumptions to review before using this

| # | Assumption | Why it matters | Default in v1 |
|---|---|---|---|
| A1 | **$25 risk cap vs. real opening-range width** | Stop = opposite OR boundary, so risk ≥ the full OR width. With $2 RT fees + 2 ticks slippage, $25 allows an OR of only **≈3.75 points** (the breakout close must be at least 1 tick beyond the range). The 15-min MES opening range is often far wider at current index levels, so **most days will be no-trade days**, and trade frequency falls as the index level rises. Measure it in `day_log.csv` (`or_width_pts`) before you draw conclusions. | Keep it. Treat a low trade count as a real finding, not a bug. |
| A2 | **$50 daily loss limit is mostly redundant** | With 1 trade/day and a $25 cap, you can only hit −$50 through a gap through the stop or a broken rule. The backtest reports any breach. | Keep it as a hard backstop. |
| A3 | **$500 account vs. margin** | Broker day-trade margin for MES varies widely and can change without notice. Exchange (overnight) margin is far above $500, so you **must be flat before the close**. One full loss is about 5% of the account, and a 10-loss streak is about 50%. | Confirm your broker's intraday margin and auto-liquidation level. |
| A4 | **MES has only existed since May 2019** | A 10-year test needs ES data as a proxy for 2015–2019. Points are identical, but fills and liquidity differ slightly. | ES 1-min data, $5/pt applied. |
| A5 | **What "first completed bar" means, and the entry order** | Not specified in the brief. | Signal = first **5-min** bar that *closes* outside the OR. Entry = **limit order at the risk-capped price** (§2 step 4), modeled as a fill at the next bar's open + 1 tick, never worse than the limit. |
| A6 | **Time-exit cutoff** | Not specified in the brief. | **12:00 ET**. Entries close at 11:00, so every trade has at least an hour to work. |
| A7 | **Target** | You named 1.5R or 2R. Testing both and picking the winner is curve-fitting. | **1.5R primary**, 2R reported as a pre-declared alternate only. |
| A8 | **Costs** | Commission + exchange + NFA fees vary by broker. | $1.00/side/contract; 1 tick slippage on entry, stop and time exits; target is a limit order that fills only if price trades 1 tick *through* it. |
| A9 | **News/event days** | Filtering news days after you've seen the results is curve-fitting. | v1 trades them; tag them in the log (FOMC, CPI, NFP) for later analysis. |
| A10 | **Costs eat a big share of a small stop** | About $4.50 of fees + slippage on a $13–25 risk. A full 1.5R target nets only **≈1.0–1.2R** after costs, so the break-even win rate is **≈45–50%**. | Nothing to set; know it when you read the results. |

---

## 1. Trigger & session window (all times US/Eastern)

| Time (ET) | Phase | Action |
|---|---|---|
| 09:00–09:29 | Pre-session | Run the **Session Checklist** (`toolbox/session_checklist.md`). Confirm platform, data, account, playbook version. |
| 09:30:00–09:44:59 | Opening range | Record the **OR High** and **OR Low** from the 15 one-minute bars. No orders. |
| 09:45 | OR lock | Compute `OR width`, then `est. risk $ = (width + 0.75) × $5 + $2`. If it is over $25, **no trade today**. Log it and stop. Otherwise compute both **limit prices**: long `OR Low + 4.25`, short `OR High − 4.25` (§2 step 4). |
| 09:45–11:00 | Entry window | **Trigger:** first completed **5-min bar** that closes **above OR High** (long) or **below OR Low** (short). |
| ≤11:00 | Last signal | A signal bar must *close* by 11:00. No new entries after that. |
| 12:00 | Time exit | Flatten any open position at market. |

**One trade per session, maximum.** The first valid signal is the only signal. If it is skipped (for example, the limit order isn't filled), the session is over. You do not wait for a "better" second breakout.

---

## 2. Step-by-step execution

1. **Lock the range (09:45).** Write OR High, OR Low and width into the trade log.
2. **Pre-check risk.** If the estimated risk is over $25 → `no_trade_reason = or_too_wide_for_risk_cap`. If the width is under 1.0 pt → `or_too_narrow`. Done for the day.
3. **Wait for a 5-min close outside the range.** Do not anticipate. Wicks don't count; only the close does.
4. **Enter** 1 MES with a **limit order at the risk-capped price** immediately after the signal bar closes.
   - Limit distance from the stop = `($25 − $2 fees) / $5 − 0.25` = 4.35 pts, rounded **down** to a tick = **4.25 pts**.
   - Long: limit = `OR Low + 4.25`. Short: limit = `OR High − 4.25`. (Recompute if you change the cap or fees.)
   - The limit guarantees you can't be filled at a price where risk exceeds $25.
5. **If not filled by the end of the next 1-minute bar, cancel.** Log `entry_limit_not_filled`, done for the day.
   *(The backtest fills only if the next bar opens at or inside the limit, a slightly pessimistic model.)*
6. **Place a bracket (OCO) immediately:**
   - Stop: the opposite OR boundary (long → OR Low, short → OR High).
   - Target: `entry ± 1.5 × |entry − stop|`, rounded to a tick *away* from entry.
7. **Hands off.** No moving the stop, no adding, no early exit, except for the escalation rules below.
8. **12:00 ET:** if still open, flatten at market.
9. **Log and close out** (§4 Proof).

### 2.1 No-trade / stand-aside rules (log the reason every time)

| Code | Condition |
|---|---|
The same codes appear in the backtest's `day_log.csv` (a test keeps this table and the code in sync).

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
| `not_ready` | Pre-session checklist not fully green | live/paper only |

A no-trade day is a **successful** day if the rules were followed. It still gets logged.

### 2.2 Escalation (stop, flatten, review)

| Trigger | Action |
|---|---|
| Platform, data or connectivity problem with a position open | Flatten immediately. Log `incident`. No more trades today. |
| Any rule violation (late entry, moved stop, second trade, oversized) | Log `rule_violation = Y`. The next 5 sessions are SIM only, even if you are live. |
| Account drawdown ≥ $100 (20%) | Pause. Compare the live-trade distribution with backtest `summary.json`. Resume only if it is within the Monte Carlo p95. |
| Account drawdown ≥ $150 (30%) | **Stop trading v1.** Return to research. A restart needs a new version and new proof. |
| Losing streak longer than backtest `losing_streak_p95` | Same as the $150 rule: the live edge may differ from the test. |
| Any single loss > $50 | Investigate the fill or slippage. Check the broker/order type before the next session. |

> **Decision pending (D6):** $150 = 6 full losses. In a simulation, a strategy losing 55–60% of trades
> has a **73–88%** chance of a 6-loss streak somewhere in 100 trades, even if it is profitable. So the
> fixed $150 stop is likely to fire on ordinary noise. Before the first real run, choose: keep it, or
> replace it with "drawdown beyond the backtest's Monte Carlo `max_dd_usd_p95`".

---

## 3. Toolbox (built once, reused every time)

| Tool | File | How it is reused (not rebuilt) |
|---|---|---|
| **Trade log** | `toolbox/trade_log_template.csv` | Copy once to `logs/trade_log_<year>.csv`. Add **one row per session**, including no-trade days. It uses the **same column names** as the backtest's `day_log.csv`/`trade_log.csv` (a test checks this), plus live-only columns (`mode`, `rule_violation`, order IDs, screenshot path). |
| **Session checklist** | `toolbox/session_checklist.md` | The same checklist every day. Copy it to `logs/sessions/YYYY-MM-DD.md` and tick the boxes. You never re-think the routine. |
| **Backtest config** | `toolbox/backtest_config.json` | The **single source of truth** for the rules. The backtest script reads it, and the playbook mirrors it. To test a variant, copy it to e.g. `backtest_config_v1.2.json`. Never edit a frozen version in place. |
| **Backtest notes / run registry** | `toolbox/backtest_notes.md` | Add one entry per run: run ID, config hash, data hash, hypothesis, result, decision. This also counts how many variants you have tried, which is your curve-fitting budget. |
| **Backtest engine** | `backtest/orb_backtest.py` | The same script for every test. Only config and data change. |
| **Smoke-test data** | `backtest/make_synthetic_data.py` | Confirms the pipeline works after any code change (`--daily-vol 0.0012` gives a set that actually trades at $25). Its results mean nothing about the market. |
| **Unit tests** | `tests/test_orb_backtest.py` | Run `python -m pytest tests/` after **any** code or rule change. Hand-built sessions with known answers, plus checks that this playbook, the template and the config agree with the code. |

---

## 4. Proof of completion ("Done" checklists)

### 4.1 Session is DONE when…

**Numbers / metrics**
- [ ] OR High, OR Low, width and estimated $ risk recorded
- [ ] Decision recorded: `TRADE` or `NO-TRADE` plus the reason code
- [ ] If traded: entry, stop, target, exit, exit reason, net $ after fees, R-multiple
- [ ] Day P&L and running account equity updated

**Links / file paths / IDs**
- [ ] Row added to `logs/trade_log_<year>.csv` with `playbook_version = v1.1`
- [ ] `logs/sessions/YYYY-MM-DD.md` checklist saved
- [ ] Broker/SIM order IDs (or the platform export file) recorded in `notes`

**Visual / chart check**
- [ ] Screenshot saved (`logs/charts/YYYY-MM-DD.png`) showing OR lines, the signal bar, entry, stop, target and exit
- [ ] The screenshot agrees with the logged prices (spot-check one level)

**Validation / rule checks**
- [ ] At most 1 trade; entry time within 09:45–11:00 ET; flat by 12:00
- [ ] Entry by limit order at or inside the risk-capped price; stop at the OR boundary; stop/target not moved
- [ ] `rule_violation` = N (if Y, escalation §2.2 applied)

### 4.2 Backtest run is DONE when…

**Numbers / metrics** (from `summary.json`, `by_year.csv`, `by_regime.csv`, `sensitivity.csv`)
- [ ] Sessions, trades, trade rate, and no-trade counts by reason
- [ ] Net P&L **after costs**, win rate, profit factor, expectancy ($ and R)
- [ ] Max drawdown in $ and as % of $500; lowest equity; longest losing streak
- [ ] Monte Carlo p95 drawdown and p95 losing streak
- [ ] Year-by-year and regime tables reviewed; no single year produces more than 50% of the profit
- [ ] Sensitivity: `costs_x2_same_trades`, `costs_x2_reselected` and the opposite ambiguity rule recorded

**Links / file paths / IDs**
- [ ] `run_id` (contains config hash + data hash) recorded in `toolbox/backtest_notes.md`
- [ ] `run_manifest.json` stored with the results; `git_commit` present and `git_worktree_dirty = false`; data file and vendor noted
- [ ] `python -m pytest tests/` passed on that commit

**Visual / chart check**
- [ ] Equity and drawdown chart reviewed (`equity_drawdown.png`, or plot `equity_curve.csv`)
- [ ] At least **10 random trades** (`trade_log.csv`) checked by hand against a chart: OR levels, signal bar, fill, exit
- [ ] At least 5 no-trade days checked (the reason is correct)

**Validation / rule checks**
- [ ] Data hygiene report reviewed (duplicates incl. conflicting ones, bad OHLC, off-grid prices, session count about 252/year, `weekdays_without_data` ≈ holidays)
- [ ] Timezone check: `open_alignment.months_flagged = 0` in `run_manifest.json`, **and** a manual look at a DST-change week (March and November)
- [ ] Rolls: `symbol` column supplied and `sessions_with_multiple_symbols` explained (or 0)
- [ ] `summary.json` → `warnings` read and each one resolved or accepted in `backtest_notes.md`
- [ ] `ambiguous_bar_share` reported; the conclusion does not flip between stop-first and target-first
- [ ] No parameter changed after seeing out-of-sample results (blueprint §2.3)

---

## 5. Version control of the playbook

- v1.0 = first commit. v1.1 = this document + `toolbox/backtest_config.json` as committed (audit fixes, before any real-data run).
- Any further change, including "just" the exit time, becomes **v1.2**: a new config file, a new backtest run, a notes entry with a reason, and a re-run of the hold-out **only if it has not been used before**.

## 6. SIM → live graduation criteria (all required)

1. Backtest passed the blueprint gates (Blueprint §4).
2. At least 40 SIM sessions logged with **zero** rule violations, and at least 15 SIM trades. *(Decision pending D7: if the measured trade rate is very low, 15 trades could take a year; decide count- vs. time-based after the first real run.)*
3. SIM fills within the modeled slippage (average ≤ 1 tick on entry and stop).
4. Broker intraday margin, auto-liquidation level and fee schedule confirmed in writing.
5. Written acceptance: *"I accept that a normal losing streak can cost $___ (Monte Carlo p95) and I will not change rules mid-streak."*

> **Risk disclosure:** futures are leveraged. Losses can exceed a planned stop (gaps, outages, fast markets). Past and simulated results do not guarantee future results. This playbook is a research and process framework, not financial advice.
