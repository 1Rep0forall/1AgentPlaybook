# Pre-Run Decisions — v1.1 (frozen at tag `v1.1-frozen`)

Each decision changes what the backtest measures. They were made on **2026-11-22, before any real
market data was loaded or run**. Values live in the place named in **Lands in**; the tests check
that the docs quote the numbers derived from the config. Changing a decision after you have seen
real results = a new version (playbook §5).

| # | Decision | Choice (2026-11-22) | Lands in | Status |
|---|---|---|---|---|
| D1 | **Risk cap** | **$25** all-in per trade on the $500 reference balance. Low trade counts or an INCONCLUSIVE verdict are accepted as valid findings, never a reason to loosen the cap mid-test. No separate research-cap config is pre-registered. | `max_risk_usd: 25.0` (derived: limit offset 4.25, max range 3.75, pre-check +0.75) | ✔ final |
| D2 | **Time exit** | **12:00 ET** hard flatten. | `exit_time: "12:00"` | ✔ final |
| D3 | **Entry order** | Risk-capped limit with attached stop. | playbook §2.1 | ✔ final |
| D4 | **Costs** | **$1.00 per side ($2.00 round turn) — PLACEHOLDER** until the exact broker all-in fee schedule (commission + exchange + NFA) is confirmed. | `commission_per_side_usd: 1.00` | ⚠ placeholder — see rule below |
| D5 | **Minimum trades** | **≥ 30 trades in the hold-out (2022→) and ≥ 100 overall**; otherwise the verdict is **INCONCLUSIVE**. | Blueprint §4 gate 4 | ✔ final |
| D6 | **Drawdown stop** | **$150** fixed (pause at $100), decided before seeing any real results. | `drawdown_pause_usd: 100.0`, `drawdown_stop_usd: 150.0` | ✔ final |
| D7 | **Paper-trading length** | **Both** ≥ 40 paper sessions **and** ≥ 15 paper trades before live is even considered. | playbook §6 item 2 | ✔ final |
| D8 | **Data spec** | Pending, see the table below. | `backtest_notes.md` pre-registration + CLI flags | ⏳ fill before the first real-data run |
| D9 | **Split** | **Dev 2015-01-01 → 2021-12-31; hold-out 2022-01-01 → present.** | `dev_start`, `dev_end`, `holdout_start` | ✔ final |
| — | **Removals** (commit `67519e4`) | Kept removed for v1.1: the `market_bail` entry mode, the 2R comparison target, and the best/worst-price excursion columns. | code + docs | ✔ final |

## D4 placeholder rule (the only allowed post-freeze edit)

A different fee changes the risk-capped limit price and the maximum range width, so it **is** a rule
change. It may be applied to v1.1 only under these conditions:

1. **Before any real-data run** (`backtest_notes.md` run log has no real-data rows): update
   `commission_per_side_usd`, run `python backtest/session_tools.py rules`, update any numbers the
   tests flag in the playbook and checklist, run the tests, log it in the change log as a
   *pre-data amendment*, commit, and tag **`v1.1-frozen-r1`**. Never move or delete `v1.1-frozen`.
   Use the newest `-rN` tag for all runs.
2. **After any real-data run** (dev included): the fee change is a **v1.2** rule change under playbook
   §5. Until then, results stay labelled as run with the $1.00/side placeholder.

## D8 data spec (fill in when you have the file, before the first run)

| Item | Value |
|---|---|
| Vendor / product | ____ |
| Instrument(s) | ES continuous (proxy for MES); MES-only cross-check 2019-05 → 2021-12 (dev only)? ____ |
| Timestamp timezone (→ `--data-tz`, or "has UTC offset") | ____ |
| Bar timestamp marks bar **start** or **end** (→ `--bar-label`) | ____ |
| Contract roll rule | ____ |
| `symbol` column present? | ____ |
| 09:20–09:29 ET pre-open bars present? | ____ |
| First date in file (needs ≥ 13 months before 2015-01-01, i.e. ≤ 2013-12-01) | ____ |
| File name and SHA-256 (the backtest prints it in `run_manifest.json`) | ____ |

Signed off: decisions D1–D7 and D9 frozen at git tag `v1.1-frozen` (`git rev-parse v1.1-frozen`
shows the commit). D8 recorded on ____ before the first real-data run.
