#!/usr/bin/env python3
"""
Generate SYNTHETIC 1-minute ES/MES-like bars for smoke-testing the backtest
pipeline end to end.

THE RESULTS ON THIS DATA MEAN NOTHING ABOUT THE REAL MARKET. It is a random
walk with volatility clustering and an opening-hour volatility bump. Use it
only to confirm the script runs, outputs are produced, and the Proof
checklist can be completed. Then replace it with real vendor data.

Each session has 09:20-15:59 ET bars (10 pre-open bars so the open-alignment
check can run) and a `symbol` column that rolls quarterly (ESH23, ESM23, ...).

    # default volatility: almost every opening range is too wide for $25
    python backtest/make_synthetic_data.py --out data/synthetic_1min.csv
    # quieter market so the $25 cap admits some trades (exercises trade logic)
    python backtest/make_synthetic_data.py --daily-vol 0.0012 --out data/synthetic_quiet.csv
"""
import argparse

import numpy as np
import pandas as pd

MONTH_CODES = {3: "H", 6: "M", 9: "U", 12: "Z"}


def front_symbol(day: pd.Timestamp) -> str:
    """Quarterly contract, rolled 8 calendar days before the 3rd-Friday expiry."""
    for year in (day.year, day.year + 1):
        for m in (3, 6, 9, 12):
            third_friday = pd.date_range(f"{year}-{m:02d}-01", periods=21, freq="D")
            third_friday = third_friday[third_friday.weekday == 4][2]
            if day < third_friday - pd.Timedelta(days=8):
                return f"ES{MONTH_CODES[m]}{year % 100:02d}"
    raise ValueError(day)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--start-price", type=float, default=3800.0)
    ap.add_argument("--daily-vol", type=float, default=0.010, help="typical daily volatility (fraction)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data/synthetic_1min.csv")
    a = ap.parse_args()

    rng = np.random.default_rng(a.seed)
    days = pd.bdate_range(a.start, a.end)
    n = 400                                            # 09:20 .. 15:59
    minutes = np.arange(n) - 10                        # minutes since 09:30
    u_shape = np.where(minutes < 0, 0.4,
                       1.0 + 1.5 * np.exp(-np.clip(minutes, 0, None) / 30)
                       + 0.5 * np.exp(-(389 - minutes) / 30))
    q = lambda x: np.round(x * 4) / 4                  # 0.25 tick grid
    price, daily_vol, frames = a.start_price, a.daily_vol, []
    for d in days:
        daily_vol = float(np.clip(0.9 * daily_vol + 0.1 * a.daily_vol * rng.lognormal(0, 0.5),
                                  a.daily_vol * 0.4, a.daily_vol * 3.5))
        price *= 1 + rng.normal(0.0003, daily_vol * 0.4)          # overnight gap
        sig = price * daily_vol / np.sqrt(390) * u_shape / u_shape[10:].mean()
        close = price + np.cumsum(rng.normal(0, sig))
        open_ = np.r_[price, close[:-1]]
        wick = np.abs(rng.normal(0, sig * 0.6, (2, n)))
        high = np.maximum(open_, close) + wick[0]
        low = np.minimum(open_, close) - wick[1]
        ts = pd.date_range(f"{d:%Y-%m-%d} 09:20", periods=n, freq="1min", tz="America/New_York")
        frames.append(pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                    "symbol": front_symbol(d),
                                    "open": q(open_), "high": q(high), "low": q(low),
                                    "close": q(close), "volume": 0}))
        price = close[-1]
    df = pd.concat(frames)
    df.to_csv(a.out, index=False)
    print(f"Wrote {len(df):,} synthetic bars ({len(days)} sessions) to {a.out}")


if __name__ == "__main__":
    main()
