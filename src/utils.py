from pathlib import Path
import os
import pandas as pd
import numpy as np
import time
import yfinance as yf
from datetime import datetime

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

def create_output_directory():
    """Create output directory if it doesn't exist."""
    project_root = Path(__file__).parent.parent
    out_dir = project_root / "output"
    
    # cria diretorio
    out_dir.mkdir(exist_ok=True)
    return out_dir

def get_timestamp():
    """Get current timestamp."""
    return datetime.now().strftime('%Y%m%d_%H%M%S')

def save_dataframe_to_csv(df, filename_prefix, output_dir):
    """Save DataFrame to CSV with timestamp."""
    if df is not None and not df.empty:
        timestamp = get_timestamp()
        filename = f"{filename_prefix}_{timestamp}.csv"
        filepath = os.path.join(output_dir, filename)
        
        df.to_csv(filepath, index=False, encoding='utf-8')
        return filepath
    else:
        return None

# df = load_position()
# print(df.head())