# 10-Year Backtest Blueprint — MES ORB v1.1

Goal: find out whether the **frozen v1.1 rules** had a positive expectancy after
realistic costs across 10 years and different regimes. The goal is not to find
the best parameters. Possible verdicts: **PASS**, **FAIL** or **INCONCLUSIVE**.
All three are useful results.

---

## 1. Data & hygiene

### 1.1 Source and coverage
- **1-minute OHLC bars** for ES (and MES from May 2019), at least 2015-01-01 → present.
  1-min data matters: with 5-min bars, far more trades have stop and target inside the same bar.
- **MES launched May 2019**, so use ES as the price proxy for the full period, and optionally
  run MES-only for 2019+ as a cross-check. Points are identical; the script applies $5/pt.
- Keep the raw vendor file read-only. Record vendor, symbol, download date and SHA-256 (the script hashes it automatically).
- **Include a `symbol` column** (contract code per bar). Without it, the script cannot detect a roll inside a session.
- **Include ≥13 months of data before your first test date** (e.g. start the file in late 2013 for a 2015 test) so regime labels are warmed up.
- **Include 09:20–09:29 ET pre-open bars** if you can: the script compares them with 09:30–09:34 to check the timezone in every month.

### 1.2 Contract roll
- ES/MES expire quarterly (Mar/Jun/Sep/Dec, third Friday). Liquidity moves to the next contract roughly a week before expiry.
- **Rule (fix it before testing):** roll when the next contract's daily volume exceeds the front's, or at a fixed calendar offset (e.g. 8 calendar days before expiry). Never mix two contracts within one session.
- **Price adjustment:** trades are intraday, so **unadjusted** prices are correct for P&L.
  Additive (difference) back-adjustment is also fine. **Never ratio-adjust**: it changes point distances, so it changes stop size and $ risk.
- Sessions containing two contract symbols are skipped (`contract_roll_in_session`). With a `symbol` column, the script also removes the roll gap from the volatility measure. The 200-day SMA trend label is still slightly distorted by roll gaps on unadjusted data; accept this or use additive-adjusted closes.

### 1.3 Session & timezone
- Anchor everything to **America/New_York** with a real tz database (the script uses pandas/zoneinfo). Never hard-code UTC−5.
- CME vendor data is often in **Central Time** or UTC. Pass `--data-tz` for naive timestamps.
- Find out whether the timestamp is the bar **start** or **end** (`--bar-label`). Getting this wrong shifts the opening range by one bar and leaks future information.
- **DST check:** the script's `open_alignment` report (in `run_manifest.json`) flags any month where 09:30–09:34 ET is not clearly more active than the reference window, which catches exports that ignore daylight saving. Also check the Monday after each March/November change by eye.
- A file that mixes timestamps with and without UTC offsets is rejected.
- Holidays: no session, so the day is skipped automatically. Weekdays with no data are logged as `no_session_data` (count ≈ exchange holidays; much more means missing data). Early closes (e.g. day after Thanksgiving, Christmas Eve): **verify the early-close time on the CME holiday calendar** for each year (this blueprint does not assert it). If data stops before 12:00, the script exits at the last bar (`data_end_exit`).

### 1.4 Hygiene checks (reported in `run_manifest.json`)
- Duplicate timestamps (and how many had *conflicting* prices), NaNs, and bars with high < max(open, close) or low > min(open, close) are dropped and counted.
- Prices must be on the 0.25 tick grid. If more than 1% of rows are off-grid the file is rejected (likely ratio-adjusted); a few stragglers are snapped and counted.
- The opening range needs all 15 one-minute bars, and each 5-min signal bar must be complete; otherwise the day is skipped and its reason logged.
- Sessions per year should be about 250–253. Big shortfalls mean missing data.
- `incomplete_or_data` no-trade days: should be rare. If there are many, fix the data and don't trust the results.
- Spot-check 5 random days against a second source (broker chart).

---

## 2. Fixed rules before testing (anti-curve-fitting)

### 2.1 Freeze
All rules live in `toolbox/backtest_config.json`. Before the first real-data run:
1. Fill in `toolbox/pre_run_decisions.md` (D1–D9) and copy the gate thresholds into `toolbox/backtest_notes.md`.
2. Commit, then tag: `git tag v1.1-frozen`. That commit is your pre-registration.
3. Every run records its git commit in `run_manifest.json`; a run from an uncommitted state doesn't count.

### 2.2 What is *not* allowed
- Tuning OR length, entry window, R-multiple, exit time or filters to improve historical results.
- Adding filters after seeing which days lost (e.g. "skip Mondays", "skip CPI days").
- Choosing a target by result. 1.5R is the only target in v1.1 (the 2R comparison was removed to take the temptation away).
- Using `mae`/`mfe`-style "what if" analysis to redesign stops and targets (these columns were removed on purpose).

### 2.3 Development vs. hold-out — governance

The script enforces the wall: every run needs `--period dev` or `--period holdout`, and a hold-out
run is refused unless the git worktree is clean.

| Period (config) | Use |
|---|---|
| `dev_start`→`dev_end` (2015-01-01 → 2021-12-31) | Repeatable. Find data problems and bugs, hand-check trades, read sensitivity. **Rules stay frozen.** |
| `holdout_start`→latest (2022-01-01 →) | **One use, ever.** Run once from the tagged commit. The result stands whatever it is. |

**Step-by-step after each run**

1. **Dev run** → work through the Proof checklist (playbook §4.2) and read the result against gates 1–3 and 5 for the dev period.
   - Data or code problem found → fix it (bug fixes need a failing test first), commit, re-run **dev**. Repeat as needed; log every run.
   - Dev result **FAIL** on gates 1–3 → **stop here. Do not run the hold-out.** v1.1 is rejected. The hold-out stays unused for a future version.
   - Dev result **INCONCLUSIVE** (too few trades) → you may still run the hold-out, but expect INCONCLUSIVE overall.
   - Dev result passes → tag the commit, then run the hold-out once.
2. **Hold-out run** → record it in `backtest_notes.md` immediately, before interpreting it.
   - **PASS** on all gates → move to paper trading (playbook §6).
   - **INCONCLUSIVE** → paper trading as data collection only; **no live trading**.
   - **FAIL** → v1.1 rejected. Write down why. No re-runs with changed rules.
3. **Bug found after the hold-out was run** → allowed only for a bug where the code demonstrably did not follow the written rules: add a failing test, fix it, re-run dev **and** hold-out with identical rules, and record both the old and new results side by side. If the fix flips the verdict, treat the result as INCONCLUSIVE and confirm it with forward paper data.
4. **Any new version (v1.2+) after the hold-out has been used** → validated on **forward paper data only** (sessions after the version's commit date). Historical data is used up for this strategy family.

Note that the hold-out covers a higher index level than the development period (check the exact
range in your data), so the fixed-point $25 cap will skip more days there. That can leave the
hold-out with very few trades (gate 4).

### 2.4 Cost & fill model (in config)
| Item | v1 assumption | Notes |
|---|---|---|
| Commission + fees | $1.00 per side per contract ($2 round trip) | Replace with your broker's all-in rate |
| Entry | **Risk-capped limit order**: fills at the next bar's **open** + 1 tick, never worse than the limit; no fill if that open is beyond the limit | Pessimistic: ignores fills later in that minute. |
| Stop | Stop price − 1 tick; if the bar **opens beyond** the stop, fill at that open − 1 tick | Models gaps and fast markets |
| Target | Limit at target, filled only if price trades **1 tick through** | Avoids assuming fills on a touch |
| Time exit | Open of first bar ≥ 12:00, − 1 tick | |
| Paper vs. model | Paper platforms usually fill limits on a touch and stops without slippage | Expect paper P&L ≥ model P&L. `session_tools.py reconcile` measures the gap daily. |
| Stress test | `costs_x2_same_trades` (same trades, 2× commission + slippage) and `costs_x2_reselected` (full re-run; the cap then admits fewer trades) | A strategy that dies at 2× costs is fragile |

### 2.5 Ambiguous bars (stop and target inside the same bar)
- OHLC bars don't reveal which price came first.
- **Default: stop first** (pessimistic). The script reports the count and share of ambiguous bars and re-runs with **target first**.
- If the conclusion changes between the two, the result is **not decidable at this bar resolution**. Resolve those bars with 1-second or tick data, or treat the test as inconclusive.
- The entry bar is included: price can hit the stop in the same minute you enter.

### 2.6 Lookahead safeguards (built into the script)
- The OR uses only bars that start 09:30–09:44 ET.
- A signal needs a **completed** 5-min bar. Entry is on the **next** bar.
- Regime labels use only data from **prior** sessions (`shift(1)`), with volatility ranked against the trailing 252 days.
- No same-day high/low or close is used to make decisions.

---

## 3. Evaluation metrics (all after costs)

| Metric | Where | Why |
|---|---|---|
| Net P&L, gross before costs, total commissions and slippage | `summary.json` | Shows how much of the edge costs eat |
| Trades, trade rate, **no-trade days by reason** | `summary.json`, `day_log.csv` | With a $25 cap, expect a low trade rate. Know why. |
| Win rate, avg win/loss, **profit factor** | `summary.json` | |
| **Expectancy** per trade ($ and R) | `summary.json` | The core edge number |
| **Max drawdown** ($, % of $500), lowest equity, first dates the pause ($100) and stop ($150) levels were reached | `summary.json` | Could you have survived it, and would the playbook have stopped you first? |
| **Longest losing streak**; longest sessions without a win | `summary.json` | Psychological and capital survivability |
| Monte Carlo (bootstrap of trade order): p50/p95 max DD, p95 losing streak, P(DD ≥ $250) | `summary.json` | Path risk the single history hides. Assumes trades are independent. Skipped below 30 trades. |
| **Year-by-year** table | `by_year.csv` | Is profit spread out or concentrated in one year? |
| **Regime** table: volatility tercile (prior 20-day ATR % vs. trailing year), trend (prior close vs. 200-day SMA) | `by_regime.csv` | Does it only work in one market type? |
| Exit-reason mix, ambiguous-bar share, daily-limit breaches | `summary.json` | Sanity and model-risk checks |
| Sensitivity: costs ×2 (same trades and re-selected), opposite ambiguity rule | `sensitivity.csv` | Robustness, **not** selection |
| `warnings` (inconclusive trade count, no symbol column, regime warm-up, timezone flags) | `summary.json` | Must be resolved or accepted in writing |

### Statistical honesty
- Standard error of expectancy ≈ `stdev(R) / √N`. As a rough guide (assuming the R-multiples have sd ≈ 1.1R; use the real value from your trade log), with N = 100 trades SE ≈ 0.11R, so an expectancy below about +0.2R is **not distinguishable from zero**.
- **Fewer than about 100 trades over 10 years means the result is inconclusive,** however good it looks. A low trade count is likely here (assumption A1).

---

## 4. Pass/fail gates (pre-registered — review and adjust *before* running)

Check gate 4 first: if there are too few trades, the verdict is **INCONCLUSIVE** whatever the other numbers say.

| # | Gate | Applies to |
|---|---|---|
| 4 | Enough trades (D5): ≥ 100 in total **and** ≥ 30 in the hold-out. Otherwise → INCONCLUSIVE. | each period |
| 1 | Expectancy > 0 after base costs **and** profit factor ≥ 1.15 | dev and hold-out separately |
| 2 | `costs_x2_same_trades` expectancy ≥ 0 | each period |
| 3 | The sign of expectancy is the same under `ambiguous_target_first` | each period |
| 5 | No single year > 50% of net profit; ≥ 60% of years with trades non-negative | full period |
| 6 | Monte Carlo p95 max drawdown < $250. If Monte Carlo was skipped (< 30 trades) → INCONCLUSIVE. | full period |
| 7 | Hand-check of 10 trades and 5 no-trade days matches the charts | each run |

**PASS** = all gates hold. **FAIL** = gate 1, 2 or 3 fails with enough trades. **INCONCLUSIVE** =
gate 4 or 6 can't be met, or gate 5/7 problems are unresolved. Record the verdict in
`backtest_notes.md` before discussing it with anyone (including an AI assistant).

---

## 5. Known limitations
- Bar-level simulation can't model queue position, partial fills or exchange halts.
- ES-proxy fills for 2015–2019 may be slightly better than MES liquidity would allow.
- Commission and margin regimes change over time; the test uses today's assumptions on old data.
- Regimes are labeled after the fact for *reporting*. They aren't trading filters.
- Bootstrap Monte Carlo ignores serial correlation (losing streaks may cluster in real markets).
