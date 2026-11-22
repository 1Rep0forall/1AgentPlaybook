# 10-Year Backtest Blueprint — MES ORB v1

Goal: find out whether the **frozen v1 rules** had a positive expectancy after
realistic costs across 10 years and different regimes. The goal is not to find
the best parameters.

---

## 1. Data & hygiene

### 1.1 Source and coverage
- **1-minute OHLC bars** for ES (and MES from May 2019), at least 2015-01-01 → present.
  1-min data matters: with 5-min bars, far more trades have stop and target inside the same bar.
- **MES launched May 2019**, so use ES as the price proxy for the full period, and optionally
  run MES-only for 2019+ as a cross-check. Points are identical; the script applies $5/pt.
- Keep the raw vendor file read-only. Record vendor, symbol, download date and SHA-256 (the script hashes it automatically).

### 1.2 Contract roll
- ES/MES expire quarterly (Mar/Jun/Sep/Dec, third Friday). Liquidity moves to the next contract roughly a week before expiry.
- **Rule (fix it before testing):** roll when the next contract's daily volume exceeds the front's, or at a fixed calendar offset (e.g. 8 calendar days before expiry). Never mix two contracts within one session.
- **Price adjustment:** trades are intraday, so **unadjusted** prices are correct for P&L.
  Additive (difference) back-adjustment is also fine. **Never ratio-adjust**: it changes point distances, so it changes stop size and $ risk.
- Daily regime indicators (ATR, 200-day SMA) are slightly distorted by roll gaps on unadjusted data. Accept this or compute them on additive-adjusted closes.

### 1.3 Session & timezone
- Anchor everything to **America/New_York** with a real tz database (the script uses pandas/zoneinfo). Never hard-code UTC−5.
- CME vendor data is often in **Central Time** or UTC. Pass `--data-tz` for naive timestamps.
- Find out whether the timestamp is the bar **start** or **end** (`--bar-label`). Getting this wrong shifts the opening range by one bar and leaks future information.
- **DST check:** on the Monday after each March/November change, confirm the 09:30 ET bar shows the opening volume spike.
- Holidays: no session, so the day is skipped automatically. Early closes (e.g. day after Thanksgiving, Christmas Eve): the futures session ends around 13:15 ET, after the 12:00 exit, so it is normally fine. The script exits at the last bar (`data_end_exit`) if data stops early.

### 1.4 Hygiene checks (reported in `run_manifest.json`)
- Duplicate timestamps, NaNs, and bars with high < max(open, close) or low > min(open, close) are dropped and counted.
- Sessions per year should be about 250–253. Big shortfalls mean missing data.
- `incomplete_or_data` no-trade days: should be rare. If there are many, fix the data and don't trust the results.
- Spot-check 5 random days against a second source (broker chart).

---

## 2. Fixed rules before testing (anti-curve-fitting)

### 2.1 Freeze
All rules live in `toolbox/backtest_config.json` v1.0 and are committed **before** the first real-data run. The commit hash is your pre-registration.

### 2.2 What is *not* allowed
- Tuning OR length, entry window, R-multiple, exit time or filters to improve historical results.
- Adding filters after seeing which days lost (e.g. "skip Mondays", "skip CPI days").
- Choosing between 1.5R and 2R by result. 1.5R is primary; 2R is reported only.

### 2.3 In-sample vs. hold-out
| Period | Use |
|---|---|
| 2015-01-01 → 2021-12-31 (development) | Find bugs, check fills by hand, run sensitivity. Rules stay frozen. Bug fixes are allowed and logged. |
| 2022-01-01 → latest (hold-out) | **Run once** with the frozen config. If it fails the gates, the result stands. Don't tweak and re-run. |

Note that the hold-out covers a different volatility and index-level regime (index roughly 3,500 → 6,900+), so the $25 cap will skip more days there. That is part of the test.

If you later create v1.1, you have spent this hold-out. Validate v1.1 on **forward SIM data**, not on the same history.

### 2.4 Cost & fill model (in config)
| Item | v1 assumption | Notes |
|---|---|---|
| Commission + fees | $1.00 per side per contract ($2 round trip) | Replace with your broker's all-in rate |
| Entry | Next bar **open** after the signal bar closes, + 1 tick adverse | A market order sent at the close |
| Stop | Stop price − 1 tick; if the bar **opens beyond** the stop, fill at that open − 1 tick | Models gaps and fast markets |
| Target | Limit at target, filled only if price trades **1 tick through** | Avoids assuming fills on a touch |
| Time exit | Open of first bar ≥ 12:00, − 1 tick | |
| Stress test | `costs_x2` scenario: double commission and slippage | A strategy that dies at 2× costs is fragile |

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
| **Max drawdown** ($, % of $500), lowest equity, first date DD ≥ $150 | `summary.json` | Could you have survived it? |
| **Longest losing streak**; longest sessions without a win | `summary.json` | Psychological and capital survivability |
| Monte Carlo (bootstrap of trade order): p50/p95 max DD, p95 losing streak, P(DD ≥ $250) | `summary.json` | Path risk the single history hides. Assumes trades are independent. |
| **Year-by-year** table | `by_year.csv` | Is profit spread out or concentrated in one year? |
| **Regime** table: volatility tercile (prior 20-day ATR % vs. trailing year), trend (prior close vs. 200-day SMA) | `by_regime.csv` | Does it only work in one market type? |
| Exit-reason mix, ambiguous-bar share, daily-limit breaches | `summary.json` | Sanity and model-risk checks |
| Sensitivity: costs ×2, opposite ambiguity rule, alternate 2R | `sensitivity.csv` | Robustness, **not** selection |

### Statistical honesty
- Standard error of expectancy ≈ `stdev(R) / √N`. With R-multiples (sd ≈ 1.1R) and N = 100 trades, SE ≈ 0.11R. An expectancy below about +0.2R is **not distinguishable from zero**.
- **Fewer than about 100 trades over 10 years means the result is inconclusive,** however good it looks. A low trade count is likely here (assumption A1).

---

## 4. Pass/fail gates (pre-registered — review and adjust *before* running)

The strategy may move to forward SIM only if **all** of these hold:

1. Development and hold-out both: expectancy > 0 after base costs, and profit factor ≥ 1.15.
2. Under `costs_x2`: expectancy ≥ 0 (not deeply negative).
3. The conclusion does not flip between stop-first and target-first ambiguity.
4. Trades ≥ 100 across the full period; otherwise the verdict is **"inconclusive — gather forward SIM data"**.
5. No single year contributes more than 50% of net profit; at least 60% of years with trades are non-negative.
6. Monte Carlo p95 max drawdown < $250 (50% of the account).
7. Hand-check of 10 trades and 5 no-trade days matches the charts.

If gates 1–3 fail: v1 is rejected. Document it. A rejected hypothesis is a valid, useful result.

---

## 5. Known limitations
- Bar-level simulation can't model queue position, partial fills or exchange halts.
- ES-proxy fills for 2015–2019 may be slightly better than MES liquidity would allow.
- Commission and margin regimes change over time; the test uses today's assumptions on old data.
- Regimes are labeled after the fact for *reporting*. They aren't trading filters.
- Bootstrap Monte Carlo ignores serial correlation (losing streaks may cluster in real markets).
