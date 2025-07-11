import logging

import pandas as pd
import pulp as pl 

from src.logger import setup_logger, get_log_filename
from src.simulator import PortfolioSimulator
from src.utils import create_output_directory, load_position, save_dataframe_to_csv 
from src import allocate

BACKTEST = True
VALOR_APORTE = 2500
K_MIN = None

third_party_loggers = ['yfinance', 'requests', 'urllib3', 'peewee', 'pulp']

for logger_name in third_party_loggers: # Desliga Debug logger dos pacotes
    logging.getLogger(logger_name).setLevel(logging.WARNING)

if __name__ == '__main__':

    setup_logger(
        log_level=logging.INFO, 
        log_file=get_log_filename() 
    )
    logger = logging.getLogger(__name__)

    out_dir = create_output_directory()

    df_port = load_position()
    VALOR_CARTEIRA = df_port['Total'].sum()

    df_aporte, sobra_final = allocate.otimiza_aporte(df = df_port, valor_carteira=VALOR_CARTEIRA, valor_aporte=VALOR_APORTE)
    # save_dataframe_to_csv(df_aporte, 'asset_rebalancing', out_dir)
    allocate.exibir_resultado_formatado(df_aporte, sobra_final, valor_aporte=VALOR_APORTE)

    optimize = allocate.otimizar_aporte_lp(df_port, valor_aporte=VALOR_APORTE, valor_carteira=VALOR_CARTEIRA, k_min=K_MIN)
    # save_dataframe_to_csv(optimize, 'asset_linear_programming', out_dir)

    if BACKTEST:
        sim     = PortfolioSimulator(df_port, valor_aporte_mensal=VALOR_APORTE, k_min_po=K_MIN)        
        df_out  = sim.simular(meses=24, data_fim_str='2025-05-01')
        save_dataframe_to_csv(df_out, 'backtest_results', out_dir)
        df_aportes = sim.obter_df_aportes()
        save_dataframe_to_csv(df_aportes, 'allocation_history', out_dir)







