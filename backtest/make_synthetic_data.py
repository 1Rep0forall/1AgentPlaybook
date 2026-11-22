#!/usr/bin/env python3
"""
Generate SYNTHETIC 1-minute ES/MES-like RTH bars for smoke-testing the
backtest pipeline end to end.

THE RESULTS ON THIS DATA MEAN NOTHING ABOUT THE REAL MARKET. It is a random
walk with volatility clustering and an opening-hour volatility bump. Use it
only to confirm the script runs, outputs are produced, and the Proof
checklist can be completed. Then replace it with real vendor data.

    python backtest/make_synthetic_data.py --start 2023-01-01 --end 2024-12-31 \
        --out data/synthetic_1min.csv
"""
import argparse

import numpy as np
import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--start-price", type=float, default=3800.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data/synthetic_1min.csv")
    a = ap.parse_args()

    rng = np.random.default_rng(a.seed)
    days = pd.bdate_range(a.start, a.end)
    minutes = np.arange(390)                          # 09:30 .. 15:59
    u_shape = 1.0 + 1.5 * np.exp(-minutes / 30) + 0.5 * np.exp(-(389 - minutes) / 30)
    price, daily_vol, frames = a.start_price, 0.010, []
    for d in days:
        daily_vol = float(np.clip(0.9 * daily_vol + 0.1 * 0.010 * rng.lognormal(0, 0.5), 0.004, 0.035))
        price *= 1 + rng.normal(0.0003, daily_vol * 0.4)          # overnight gap
        sig = price * daily_vol / np.sqrt(390) * u_shape / u_shape.mean()
        close = price + np.cumsum(rng.normal(0, sig))
        open_ = np.r_[price, close[:-1]]
        wick = np.abs(rng.normal(0, sig * 0.6, (2, 390)))
        high = np.maximum(open_, close) + wick[0]
        low = np.minimum(open_, close) - wick[1]
        q = lambda x: np.round(x * 4) / 4                          # 0.25 tick grid
        ts = pd.date_range(f"{d:%Y-%m-%d} 09:30", periods=390, freq="1min", tz="America/New_York")
        frames.append(pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                    "open": q(open_), "high": q(high), "low": q(low),
                                    "close": q(close), "volume": 0}))
        price = close[-1]
    df = pd.concat(frames)
    df.to_csv(a.out, index=False)
    print(f"Wrote {len(df):,} synthetic bars ({len(days)} sessions) to {a.out}")


if __name__ == "__main__":
    main()
