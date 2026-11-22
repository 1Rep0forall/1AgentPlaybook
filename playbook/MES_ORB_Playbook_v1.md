# MES ORB Playbook — v1.0

> **One Agent + Playbook + Toolbox + Proof.** The agent (you, or an AI assistant)
> runs the *same* playbook every session, uses the *same* tools, and does not call
> anything "done" without the *same* proof. Rules change only by creating v1.1, v2, etc.

**Status:** SIM / paper only. No live capital until the graduation criteria (§6) are met.
**Rules source of truth:** `toolbox/backtest_config.json` (the backtest and live rules must match).

---

## 0. Assumptions to review before using this

| # | Assumption | Why it matters | Default in v1 |
|---|---|---|---|
| A1 | **$25 risk cap vs. real opening-range width** | Stop = opposite OR boundary, so risk ≥ the full OR width. With $2 RT fees + 2 ticks slippage, $25 allows an OR of only **≈4 points**. The 15-min MES opening range is often far wider at current index levels, so **most days will be no-trade days**, and trade frequency falls as the index level rises. Measure it in `day_log.csv` (`or_width_pts`) before you draw conclusions. | Keep it. Treat a low trade count as a real finding, not a bug. |
| A2 | **$50 daily loss limit is mostly redundant** | With 1 trade/day and a $25 cap, you can only hit −$50 through a gap through the stop or a broken rule. The backtest reports any breach. | Keep it as a hard backstop. |
| A3 | **$500 account vs. margin** | Broker day-trade margin for MES varies widely and can change without notice. Exchange (overnight) margin is far above $500, so you **must be flat before the close**. One full loss is about 5% of the account, and a 10-loss streak is about 50%. | Confirm your broker's intraday margin and auto-liquidation level. |
| A4 | **MES has only existed since May 2019** | A 10-year test needs ES data as a proxy for 2015–2019. Points are identical, but fills and liquidity differ slightly. | ES 1-min data, $5/pt applied. |
| A5 | **What "first completed bar" means** | Not specified in the brief. | Signal = first **5-min** bar that *closes* outside the OR. Entry = **next bar's open** + 1 tick (you can't fill at a close you only see after it prints). |
| A6 | **Time-exit cutoff** | Not specified in the brief. | **12:00 ET**. Entries close at 11:00, so every trade has at least an hour to work. |
| A7 | **Target** | You named 1.5R or 2R. Testing both and picking the winner is curve-fitting. | **1.5R primary**, 2R reported as a pre-declared alternate only. |
| A8 | **Costs** | Commission + exchange + NFA fees vary by broker. | $1.00/side/contract; 1 tick slippage on entry, stop and time exits; target is a limit order that fills only if price trades 1 tick *through* it. |
| A9 | **News/event days** | Filtering news days after you've seen the results is curve-fitting. | v1 trades them; tag them in the log (FOMC, CPI, NFP) for later analysis. |

---

## 1. Trigger & session window (all times US/Eastern)

| Time (ET) | Phase | Action |
|---|---|---|
| 09:00–09:29 | Pre-session | Run the **Session Checklist** (§3.2). Confirm platform, data, account, playbook version. |
| 09:30:00–09:44:59 | Opening range | Record the **OR High** and **OR Low** from the 15 one-minute bars. No orders. |
| 09:45 | OR lock | Compute `OR width`, then `est. risk $ = (width + 0.50) × $5 + $2`. If it is over $25, **no trade today**. Log it and stop. |
| 09:45–11:00 | Entry window | **Trigger:** first completed **5-min bar** that closes **above OR High** (long) or **below OR Low** (short). |
| ≤11:00 | Last signal | A signal bar must *close* by 11:00. No new entries after that. |
| 12:00 | Time exit | Flatten any open position at market. |

**One trade per session, maximum.** The first valid signal is the only signal. If it is skipped (for example, risk is too large at the actual fill), the session is over. You do not wait for a "better" second breakout.

---

## 2. Step-by-step execution

1. **Lock the range (09:45).** Write OR High, OR Low and width into the trade log.
2. **Pre-check risk.** If the estimated risk is over $25 → `no_trade_reason = or_too_wide_for_risk_cap`. If the width is under 1.0 pt → `or_too_narrow`. Done for the day.
3. **Wait for a 5-min close outside the range.** Do not anticipate. Wicks don't count; only the close does.
4. **Enter** 1 MES at market immediately after the signal bar closes.
5. **Re-check risk at the actual fill:** `(|fill − stop| + 0.25) × $5 + $2 ≤ $25`. If it fails, exit at once, log `risk_cap_at_entry`, done.
   *(In SIM, practice computing this before you click. The backtest skips these trades.)*
6. **Place a bracket (OCO) immediately:**
   - Stop: the opposite OR boundary (long → OR Low, short → OR High).
   - Target: `entry ± 1.5 × |entry − stop|`, rounded to a tick *away* from entry.
7. **Hands off.** No moving the stop, no adding, no early exit, except for the escalation rules below.
8. **12:00 ET:** if still open, flatten at market.
9. **Log and close out** (§4 Proof).

### 2.1 No-trade / stand-aside rules (log the reason every time)

| Code | Condition |
|---|---|
| `or_too_wide_for_risk_cap` | Estimated risk over $25 at 09:45 |
| `or_too_narrow` | OR width under 1.0 point |
| `no_breakout` | No 5-min close outside the OR by 11:00 |
| `risk_cap_at_entry` | The actual fill makes risk over $25 |
| `daily_loss_limit` | Day P&L ≤ −$50 (hard stop for the day) |
| `incomplete_or_data` | Data or platform problem during 09:30–09:45 |
| `holiday_or_half_day` | Exchange holiday, or the session closes early before the time exit |
| `not_ready` | Pre-session checklist not fully green |

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

---

## 3. Toolbox (built once, reused every time)

| Tool | File | How it is reused (not rebuilt) |
|---|---|---|
| **Trade log** | `toolbox/trade_log_template.csv` | Copy once to `logs/trade_log_<year>.csv`. Add **one row per session**, including no-trade days. Columns match the backtest's `day_log.csv` and `trade_log.csv`, so live/SIM results can be compared directly with backtest output. |
| **Session checklist** | `toolbox/session_checklist.md` | The same checklist every day. Copy it to `logs/sessions/YYYY-MM-DD.md` and tick the boxes. You never re-think the routine. |
| **Backtest config** | `toolbox/backtest_config.json` | The **single source of truth** for the rules. The backtest script reads it, and the playbook mirrors it. To test a variant, copy it to `backtest_config_v1.1.json`. Never edit the frozen v1 in place. |
| **Backtest notes / run registry** | `toolbox/backtest_notes.md` | Add one entry per run: run ID, config hash, data hash, hypothesis, result, decision. This also counts how many variants you have tried, which is your curve-fitting budget. |
| **Backtest engine** | `backtest/orb_backtest.py` | The same script for every test. Only config and data change. |
| **Smoke-test data** | `backtest/make_synthetic_data.py` | Confirms the pipeline works after any code change. Its results mean nothing about the market. |

---

## 4. Proof of completion ("Done" checklists)

### 4.1 Session is DONE when…

**Numbers / metrics**
- [ ] OR High, OR Low, width and estimated $ risk recorded
- [ ] Decision recorded: `TRADE` or `NO-TRADE` plus the reason code
- [ ] If traded: entry, stop, target, exit, exit reason, net $ after fees, R-multiple
- [ ] Day P&L and running account equity updated

**Links / file paths / IDs**
- [ ] Row added to `logs/trade_log_<year>.csv` with `playbook_version = v1.0`
- [ ] `logs/sessions/YYYY-MM-DD.md` checklist saved
- [ ] Broker/SIM order IDs (or the platform export file) recorded in `notes`

**Visual / chart check**
- [ ] Screenshot saved (`logs/charts/YYYY-MM-DD.png`) showing OR lines, the signal bar, entry, stop, target and exit
- [ ] The screenshot agrees with the logged prices (spot-check one level)

**Validation / rule checks**
- [ ] At most 1 trade; entry time within 09:45–11:00 ET; flat by 12:00
- [ ] Risk ≤ $25 at entry; stop at the OR boundary; stop/target not moved
- [ ] `rule_violation` = N (if Y, escalation §2.2 applied)

### 4.2 Backtest run is DONE when…

**Numbers / metrics** (from `summary.json`, `by_year.csv`, `by_regime.csv`, `sensitivity.csv`)
- [ ] Sessions, trades, trade rate, and no-trade counts by reason
- [ ] Net P&L **after costs**, win rate, profit factor, expectancy ($ and R)
- [ ] Max drawdown in $ and as % of $500; lowest equity; longest losing streak
- [ ] Monte Carlo p95 drawdown and p95 losing streak
- [ ] Year-by-year and regime tables reviewed; no single year produces more than 50% of the profit
- [ ] Sensitivity: result under `costs_x2` and the opposite ambiguity rule recorded

**Links / file paths / IDs**
- [ ] `run_id` (contains config hash + data hash) recorded in `toolbox/backtest_notes.md`
- [ ] `run_manifest.json` stored with the results; data file and vendor noted

**Visual / chart check**
- [ ] Equity and drawdown chart reviewed (`equity_drawdown.png`, or plot `equity_curve.csv`)
- [ ] At least **10 random trades** (`trade_log.csv`) checked by hand against a chart: OR levels, signal bar, fill, exit
- [ ] At least 5 no-trade days checked (the reason is correct)

**Validation / rule checks**
- [ ] Data hygiene report reviewed (duplicates, bad OHLC, session count about 252/year)
- [ ] Timezone check: 09:30 ET bars line up with the cash open on a DST-change week (March and November)
- [ ] `ambiguous_bar_share` reported; the conclusion does not flip between stop-first and target-first
- [ ] No parameter changed after seeing out-of-sample results (blueprint §2.3)

---

## 5. Version control of the playbook

- v1.0 = this document + `toolbox/backtest_config.json` as committed.
- Any change, including "just" the exit time, becomes **v1.1**: a new config file, a new backtest run, a notes entry with a reason, and a re-run of the hold-out **only if it has not been used before**.

## 6. SIM → live graduation criteria (all required)

1. Backtest passed the blueprint gates (Blueprint §4).
2. At least 40 SIM sessions logged with **zero** rule violations, and at least 15 SIM trades.
3. SIM fills within the modeled slippage (average ≤ 1 tick on entry and stop).
4. Broker intraday margin, auto-liquidation level and fee schedule confirmed in writing.
5. Written acceptance: *"I accept that a normal losing streak can cost $___ (Monte Carlo p95) and I will not change rules mid-streak."*

> **Risk disclosure:** futures are leveraged. Losses can exceed a planned stop (gaps, outages, fast markets). Past and simulated results do not guarantee future results. This playbook is a research and process framework, not financial advice.
