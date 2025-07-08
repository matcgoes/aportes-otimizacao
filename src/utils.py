from pathlib import Path
import pandas as pd
import numpy as np
import time
import yfinance as yf

DATA_DIR = Path(__file__).parents[1] / "data"

def load_position():
    df = pd.read_csv(DATA_DIR / "fake_data_port.csv", sep=';', index_col=0)
    return df

def _download_with_retry(ticker, start, end, attempts=3):
    for _ in range(attempts):
        try:
            df = yf.download(
                ticker, start=start, end=end,
                progress=False, auto_adjust=False, threads=False
            )
            if not df.empty:
                return df
        except Exception:
            pass
        time.sleep(1)
    return pd.DataFrame()

# df = load_position()
# print(df.head())