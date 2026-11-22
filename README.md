# 1AgentPlaybook — MES Opening Range Breakout (v1.1, frozen at tag `v1.1-frozen`)

A rules-based research and paper-trading workflow for a 15-minute Opening Range
Breakout on Micro E-mini S&P 500 futures (MES), built on the
**One Agent + Playbook + Toolbox + Proof** approach.

> **Paper trading only.** Nothing here has been tested on real market data yet: the only runs so
> far used synthetic random-walk data to check the code. This is a research framework, not
> financial advice. Futures are leveraged; losses can exceed planned stops.

| Part | Path |
|---|---|
| Agent instructions (daily loop, boundaries) | `CLAUDE.md` |
| Playbook (rules, no-trade codes, escalation, proof, change process) | `playbook/MES_ORB_Playbook_v1.md` |
| 10-year backtest blueprint (method, hold-out governance, gates) | `playbook/Backtest_Blueprint.md` |
| Rules config (frozen at `v1.1-frozen`; fee is a $1.00/side placeholder, D4) | `toolbox/backtest_config.json` |
| Pre-run decisions D1–D9 | `toolbox/pre_run_decisions.md` |
| Trade log template / session checklist | `toolbox/trade_log_template.csv`, `toolbox/session_checklist.md` |
| Run registry, change log, pause reviews | `toolbox/backtest_notes.md` |
| Backtest engine | `backtest/orb_backtest.py` |
| Session tools: `rules`, `status`, `reconcile` | `backtest/session_tools.py` |
| Local dashboard (GUI) | `gui/server.py`, `gui/index.html` |
| Local API-key storage + connection test | `backtest/credentials.py` |
| Synthetic smoke-test data | `backtest/make_synthetic_data.py` |
| Tests (logic + docs/config consistency) | `tests/test_orb_backtest.py` |

## Dashboard (one command)
```bash
python gui/server.py
```
Then open http://127.0.0.1:8765 (add `--open` to open the browser automatically, `--port N` to change the port).
Standard library only; it listens on 127.0.0.1 and nothing else.

- **System status:** GO / PAUSE / STOP / FIX-PROOF from your paper log (`logs/trade_log_<year>.csv`), equity and
  drawdown against the $100 pause and $150 stop, and progress toward the 40-session / 15-trade paper gate.
- **Today's session rules:** the same numbers as `session_tools.py rules`, taken from the frozen config.
- **Actions:** Status check, Run unit tests, Run dev backtest (2015–2021 only), Reconcile session. Each runs the
  same CLI script and shows its output. There is **no hold-out button**: that one-time run stays a CLI action.
- **API settings:** data-provider and broker keys go to `config/secrets.local.json` (git-ignored, file mode 0600)
  and are never shown again (only the last 4 characters). Live mode is locked until playbook §6 is met.
  - *Test connection* is a generic check, because no provider-specific connector is built yet. You give an
    endpoint that **requires** authentication; it must refuse a request without the key and accept one with it
    (Bearer key, or Basic key:secret for the broker). Only that shows a green **Connected**. Anything else is red:
    Not configured, Auth failed, Unreachable, or Not verified (the URL answered without a key, so it proves nothing).
  - CLI scripts can read the same keys with `from credentials import load_credentials`. Environment variables
    `ORB_DATA_API_KEY`, `ORB_BROKER_API_KEY` and `ORB_BROKER_API_SECRET` override the file.
    `python backtest/credentials.py show` prints a masked summary.

## Quick start (CLI)
```bash
pip install pandas numpy pytest     # matplotlib optional (equity chart)
python -m pytest tests/ -q
python backtest/session_tools.py rules
```
Smoke test (synthetic data, 2016–2017, inside the dev period; results mean nothing):
```bash
python backtest/make_synthetic_data.py --daily-vol 0.0012 --out data/synthetic_quiet.csv
python backtest/orb_backtest.py --period dev --data data/synthetic_quiet.csv --out /tmp/orb_smoke
```
Real data (fill in the D8 data spec first; example: naive Central-Time 1-min ES bars stamped at bar start):
```bash
python backtest/orb_backtest.py --period dev --data data/es_1min.csv --data-tz America/Chicago --bar-label start --config toolbox/backtest_config.json
```
`--period holdout` is a **one-time** run (Blueprint §2.3) and is refused on an uncommitted worktree.
See the docstring at the top of `backtest/orb_backtest.py` for the input CSV format.

## What git tracks
`logs/` (paper-trading proof) and `results/` (backtest outputs) are committed; that is the proof.
Ignored: raw market data in `data/`, and **local secrets** (`config/`, `.env`, `*.local.json`). The
credentials code refuses to write keys anywhere that isn't git-ignored.

## The biggest thing to understand first
The stop sits at the opposite side of the opening range, so dollar risk is at least the full
range width. Under a $25 cap (after $2 fees and 3 ticks of breakout + slippage), only opening
ranges of about **3.75 points or less** qualify. Most sessions will be no-trade days, and a low
trade count may make the 10-year result **inconclusive**. That is an honest outcome, not a bug.
