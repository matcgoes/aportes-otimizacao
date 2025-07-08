import pandas as pd
import numpy as np
import pulp as pl
import time
from datetime import datetime, timedelta
import yfinance as yf
import requests
import pulp as pl
import logging
from src.utils import load_position, _download_with_retry
from src.logger import setup_logger
from src.simulator import PortfolioSimulator
from src import allocate

BACKTEST = True

if __name__ == '__main__':

    logger = setup_logger('app_main_logger')

    logger.info('Reading data...')
    df_port = load_position()
    VALOR_CARTEIRA = df_port['Total'].sum()
    VALOR_APORTE = 2500
    K_MIN = None

    logger.info('Teste...')

    df_aporte, sobra_final = allocate.otimiza_aporte(df = df_port, valor_carteira=VALOR_CARTEIRA, valor_aporte=VALOR_APORTE)
    logger.info('Test3...')
    allocate.exibir_resultado_formatado(df_aporte, sobra_final, valor_aporte=VALOR_APORTE)

    logger.info('Test4...')
    optimize = allocate.otimizar_aporte_lp(df_port, valor_aporte=VALOR_APORTE, valor_carteira=VALOR_CARTEIRA, k_min=K_MIN)

    if BACKTEST:
        logger.info('Test5...')
        sim     = PortfolioSimulator(df_port, valor_aporte_mensal=VALOR_APORTE, k_min_po=K_MIN, logger=logger)
        logger.info('Test6...')
        df_out  = sim.simular(meses=24, data_fim_str='2025-05-01')
        logger.info('Test7...')
        print(df_out.head())
        df_aportes = sim.obter_df_aportes()
        print(df_aportes.head())







