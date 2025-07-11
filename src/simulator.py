import time
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import yfinance as yf
import requests
import pulp as pl
import logging
from src.utils import _download_with_retry

logger = logging.getLogger(__name__)

class PortfolioSimulator:
    def __init__(self, df_portfolio, valor_aporte_mensal=2500, k_min_po=None):
        logger.info("Initializing PortfolioSimulator")
        logger.debug(f"Portfolio shape: {df_portfolio.shape}")
        logger.debug(f"Portfolio columns: {df_portfolio.columns.tolist()}")
        
        self.df_original = df_portfolio.copy()
        if self.df_original["% Ideal - Ref."].max() > 1:
            logger.debug("Converting percentage values from 0-100 to 0-1 format")
            self.df_original["% Ideal - Ref."] /= 100.0
        
        self.aporte_mensal = valor_aporte_mensal
        self.k_min_po = k_min_po
        # ← lista de classes p/ cálculo de drift
        self.classes = sorted(self.df_original["Classe"].unique())
        logger.debug(f"Available asset classes: {self.classes}")
        
        # Nova estrutura para armazenar aportes detalhados
        self.aportes_detalhados = []

        logger.info(f"PortfolioSimulator initialized with capital: R$ {valor_aporte_mensal:,.2f}")
        logger.debug(f"Minimum portfolio optimization assets: {k_min_po}")

    # ------------- mapeamento de tickers -----------------
    def mapear_tickers(self):
        """Apenas ativos negociados via Yahoo; RF especial fica com próprio nome."""
        logger.info('Mapping portfolio tickers...')
        mapa = {}
        fixed_income_count = 0
        br_stocks_count = 0
        us_stocks_count = 0
        
        for _, row in self.df_original.iterrows():
            tk, geo = row["Ticker"], row["Geo."]
            if tk in {"SELIC", "FDI", "FRFH", "LC", "CDB",
                    "IPCA", "CDBI", "PRE", "PGBL"}:
                mapa[tk] = None          # marcador especial
                fixed_income_count += 1
            elif geo == "BR":
                mapa[tk] = f"{tk}.SA"
                br_stocks_count += 1
            elif geo == "US":
                mapa[tk] = tk
                us_stocks_count += 1
        
        logger.info(f"Ticker mapping completed: {fixed_income_count} fixed income, {br_stocks_count} BR stocks, {us_stocks_count} US stocks")
        logger.debug(f"Ticker mapping: {mapa}")
        return mapa
    
    @staticmethod
    def _selic_fator_mensal(start, end):
        """SGS 4390 Selic diária → fator acumulado em datas 'M' (últ. dia útil do mês)."""
        logger.info(f'Getting SELIC Index from {start.strftime("%Y-%m-%d")} to {end.strftime("%Y-%m-%d")}')
        try:
            url = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.4390/dados"
                f"?formato=json&dataInicial={start:%d/%m/%Y}&dataFinal={end:%d/%m/%Y}")
            logger.debug(f"SELIC API URL: {url}")
            js  = requests.get(url, timeout=10).json()
            logger.debug(f"SELIC API returned {len(js)} records")
            df  = pd.DataFrame(js)
            df["data"]  = pd.to_datetime(df["data"], format="%d/%m/%Y")
            df["valor"] = df["valor"].astype(float) / 100        # diário (fração)
            df["fator"] = (1 + df["valor"]).cumprod()
            fator_m = df.set_index("data").resample("M").last()["fator"]
            logger.info(f"SELIC data processed successfully: {len(fator_m)} monthly factors")
            return fator_m
        except Exception as e:
            logger.error(f"Error fetching SELIC data: {str(e)}")
            raise

    @staticmethod
    def _ipca_fator_mensal(start, end):
        logger.info(f'Getting IPCA Index from {start.strftime("%Y-%m-%d")} to {end.strftime("%Y-%m-%d")}')
        """SGS 433 IPCA % mensal → fator acumulado (base 1)."""
        try:
            url = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados"
                f"?formato=json&dataInicial={start:%d/%m/%Y}&dataFinal={end:%d/%m/%Y}")
            logger.debug(f"IPCA API URL: {url}")
            js  = requests.get(url, timeout=10).json()
            logger.debug(f"IPCA API returned {len(js)} records")
            df  = pd.DataFrame(js)
            df["data"]  = pd.to_datetime(df["data"], format="%d/%m/%Y") + pd.offsets.MonthEnd(0)
            df["valor"] = df["valor"].astype(float) / 100
            df["fator"] = (1 + df["valor"]).cumprod()
            logger.info(f"IPCA data processed successfully: {len(df)} monthly factors")
            return df.set_index("data")["fator"]
        except Exception as e:
            logger.error(f"Error fetching IPCA data: {str(e)}")
            raise

    @staticmethod
    def _fator_mensal_fixo(start, end, taxa_anual):
        """Retorna série mensal de fator acumulado (base 1.0) para uma taxa 'anual' constante."""
        logger.debug(f"Calculating fixed monthly factor for annual rate {taxa_anual:.2%}")
        idx = pd.date_range(start, end, freq="M")
        taxa_mensal = (1 + taxa_anual) ** (1/12) - 1          # ⇐ conversão
        result = pd.Series((1 + taxa_mensal) ** np.arange(1, len(idx)+1), index=idx)
        logger.debug(f"Fixed factor series created with {len(result)} months")
        return result

    # ------------- históricos ----------------------------
    def obter_dados_historicos(self, meses, data_fim_str=None):
        logger.info(f'Getting Historical Ticker Prices for {meses} months')
        
        if data_fim_str:
            end_date = datetime.strptime(data_fim_str, '%Y-%m-%d')
            logger.info(f"Using custom end date: {end_date.strftime('%Y-%m-%d')}")
        else:
            end_date = datetime.now()
            logger.info(f"Using current date as end: {end_date.strftime('%Y-%m-%d')}")
       
        start_date = end_date - timedelta(days=meses*30)
        logger.info(f"Data collection period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")

        dados, mapa = {}, self.mapear_tickers()

        # Get USD/BRL exchange rate
        logger.info("Fetching USD/BRL exchange rate")
        try:
            usd = _download_with_retry("USDBRL=X", start_date, end_date)["Adj Close"]
            if usd.empty:
                raise RuntimeError("Falha USD/BRL")
            logger.info(f"USD/BRL data collected: {len(usd)} records")
        except Exception as e:
            logger.error(f"Error fetching USD/BRL: {str(e)}")
            raise

        print("Coletando séries…")
        logger.info(f"Starting data collection for {len(mapa)} tickers")
        
        for i, (tk, sym) in enumerate(mapa.items(), 1):
            logger.debug(f"Processing ticker {i}/{len(mapa)}: {tk} ({sym})")
            
            # ─── Renda-fixa e índices ────────────────────────────────
            if tk in {"SELIC", "FDI", "FRFH", "LC", "CDB"}:
                logger.debug(f"Processing {tk} as SELIC-based fixed income")
                try:
                    dados[tk] = self._selic_fator_mensal(start_date, end_date)
                    logger.debug(f"Successfully processed {tk}: {len(dados[tk])} records")
                except Exception as e:
                    logger.error(f"Error processing {tk}: {str(e)}")
                continue
                
            if tk in {"IPCA", "CDBI"}:
                logger.debug(f"Processing {tk} as IPCA-based fixed income")
                try:
                    dados[tk] = self._ipca_fator_mensal(start_date, end_date)
                    logger.debug(f"Successfully processed {tk}: {len(dados[tk])} records")
                except Exception as e:
                    logger.error(f"Error processing {tk}: {str(e)}")
                continue
                
            if tk == "PRE":
                logger.debug(f"Processing {tk} as fixed rate (9% p.a.)")
                dados[tk] = self._fator_mensal_fixo(start_date, end_date, 0.09)   # 9 % a.a.
                logger.debug(f"Successfully processed {tk}: {len(dados[tk])} records")
                continue
                
            if tk == "PGBL":
                logger.debug(f"Processing {tk} as fixed rate (7% p.a.)")
                dados[tk] = self._fator_mensal_fixo(start_date, end_date, 0.07)   # 7 % a.a.
                logger.debug(f"Successfully processed {tk}: {len(dados[tk])} records")
                continue

            # ─── Ações / ETFs ───────────────────────────────────────
            logger.debug(f"Processing {tk} as stock/ETF from Yahoo Finance")
            try:
                dfp = _download_with_retry(sym, start_date, end_date)
                if dfp.empty:
                    logger.warning(f"No data available for {tk} ({sym})")
                    print(f"Sem dados {tk}")
                    continue
                
                serie_m = dfp["Adj Close"].resample("M").last()
                logger.debug(f"Retrieved {len(serie_m)} monthly prices for {tk}")
                
                # Convert US stocks to BRL
                if self.df_original.loc[self.df_original["Ticker"] == tk, "Geo."].iat[0] == "US":
                    logger.debug(f"Converting {tk} from USD to BRL")
                    usd_m = usd.resample("M").last()
                    serie_m.values[:] = serie_m.to_numpy()*usd_m.to_numpy()
                    logger.debug(f"Currency conversion completed for {tk}")

                dados[tk] = serie_m
                logger.debug(f"Successfully processed {tk}: {len(dados[tk])} records")
                
            except Exception as e:
                logger.error(f"Error processing {tk}: {str(e)}")
                continue

        logger.info(f"Historical data collection completed: {len(dados)} tickers processed")
        return dados

    # ------------- estratégia 1 -------------------------
    def _aporte_deficit(self, df, valor_cart, aporte, mes, data):
        logger.info(f'Strategy 1: Asset by Rebalancing Portfolio - Month {mes}')
        logger.debug(f"Portfolio value: R$ {valor_cart:,.2f}, Monthly contribution: R$ {aporte:,.2f}")
        
        df = df.copy()
        df["Valor Ideal"] = df["% Ideal - Ref."] * (valor_cart + aporte)
        df["deficit"] = (df["Valor Ideal"] - df["Total"]).clip(lower=0)
        
        total_deficit = df["deficit"].sum()
        logger.debug(f"Total deficit: R$ {total_deficit:,.2f}")
        
        if total_deficit == 0:
            logger.info("No deficit found, no allocation needed")
            return df, aporte
            
        df["aporte_sugerido"] = df["deficit"] / total_deficit * aporte
        df["Qtd_comprar"], df["Custo_real"] = 0.0, 0.0
        
        total_cost = 0
        assets_bought = 0
        
        for i, r in df.iterrows():
            if r["aporte_sugerido"] > 0:
                qtd = (r["aporte_sugerido"] / r["Cotação"]) if (r["Classe"] == "RF" and r["Ticker"] != "IMAB11") \
                      else np.floor(r["aporte_sugerido"] / r["Cotação"])
                df.at[i, "Qtd_comprar"] = qtd
                df.at[i, "Custo_real"]  = qtd * r["Cotação"]
                total_cost += qtd * r["Cotação"]
                
                if qtd > 0:
                    assets_bought += 1
                    logger.debug(f"Buying {qtd} shares of {r['Ticker']} at R$ {r['Cotação']:.2f} = R$ {qtd * r['Cotação']:.2f}")
                    # Salvar detalhes do aporte se houve compra
                    self._salvar_aporte_detalhado(r, qtd, qtd * r["Cotação"], "Deficit", mes, data, df)

        leftover = aporte - total_cost
        logger.info(f"Deficit strategy completed: {assets_bought} assets bought, R$ {total_cost:,.2f} invested, R$ {leftover:,.2f} leftover")
        return df, leftover

    # ------------- estratégia 2 -------------------------
    def _aporte_po(self, df, valor_cart, aporte, mes, data):
        logger.info(f'Strategy 2: Asset by Linear Programming Portfolio optimization - Month {mes}')
        logger.debug(f"Portfolio value: R$ {valor_cart:,.2f}, Monthly contribution: R$ {aporte:,.2f}")
        
        df = df.copy()
        if df.empty:
            logger.warning("Empty portfolio dataframe, falling back to deficit strategy")
            return self._aporte_deficit(df, valor_cart, aporte, mes, data)

        df["Valor Ideal"] = df["% Ideal - Ref."] * (valor_cart + aporte)
        df["deficit"] = (df["Valor Ideal"] - df["Total"]).clip(lower=0)
        
        total_deficit = df["deficit"].sum()
        logger.debug(f"Total deficit: R$ {total_deficit:,.2f}")
        
        logger.debug("Setting up linear programming problem")
        prob, qt_rf, qt_rv, gap = pl.LpProblem("PO", pl.LpMinimize), {}, {}, {}
        sel = {}                                    # só usado se houver limite

        variables_created = 0
        for idx, r in df.iterrows():
            nome = str(r["Ativo"]).replace(" ", "_").replace(".", "_")

            # ----- variável de seleção (se houver limite K) -------------
            if self.k_min_po:                       # None/0 = não cria
                sel[idx] = pl.LpVariable(f"SEL_{nome}", cat="Binary")
                variables_created += 1

            # ----- quantidade a comprar ---------------------------------
            if r["Classe"] == "RF" and r["Ticker"] != "IMAB11":
                qt_rf[idx] = pl.LpVariable(f"RF_{nome}", lowBound=0)
                variables_created += 1
            else:
                qt_rv[idx] = pl.LpVariable(f"RV_{nome}", lowBound=0, cat="Integer")
                variables_created += 1

            gap[idx] = pl.LpVariable(f"GAP_{nome}", lowBound=0)
            variables_created += 1

        logger.debug(f"Created {variables_created} variables for optimization")

        # -------- objetivo: minimizar gaps ------------------------------
        prob += pl.lpSum(gap.values())
        logger.debug("Objective function set: minimize gaps")
        
        # -------- restrição de orçamento --------------------------------
        prob += (
            pl.lpSum(df.loc[i, "Cotação"] * qt_rf[i] for i in qt_rf)
          + pl.lpSum(df.loc[i, "Cotação"] * qt_rv[i] for i in qt_rv)
        ) <= aporte
        logger.debug("Budget constraint added")

        # -------- cardinalidade (se houver K) ---------------------------
        if self.k_min_po:
            logger.debug(f"Adding cardinality constraint: minimum {self.k_min_po} assets")
            prob += pl.lpSum(sel.values()) >= self.k_min_po
            
            constraints_added = 0
            for idx in df.index:
                preco = df.at[idx, "Cotação"]
                max_qtd = aporte / preco
                min_qtd = 1 if idx in qt_rv else (1/preco)
                if idx in qt_rf:
                    prob += qt_rf[idx] >= min_qtd * sel[idx]
                    prob += qt_rf[idx] <= max_qtd * sel[idx]
                else:
                    prob += qt_rv[idx] >= min_qtd * sel[idx]
                    prob += qt_rv[idx] <= max_qtd * sel[idx]
                constraints_added += 2
            logger.debug(f"Added {constraints_added} cardinality constraints")

        # Gap constraints
        gap_constraints = 0
        for idx in df.index:
            preco = df.at[idx, "Cotação"]
            compra = preco * (qt_rf[idx] if idx in qt_rf else qt_rv[idx])
            prob += gap[idx] >= df.at[idx,"deficit"] - compra
            gap_constraints += 1
        logger.debug(f"Added {gap_constraints} gap constraints")

        # Solve optimization problem
        logger.debug("Solving optimization problem")
        try:
            prob.solve(pl.PULP_CBC_CMD(msg=0))
            logger.debug(f"Optimization status: {pl.LpStatus[prob.status]}")
        except pl.PulpError as e:
            logger.error(f"PulpError during optimization: {str(e)}")
            logger.debug("Attempting to solve with default solver")
            prob.solve()

        if prob.status != pl.LpStatusOptimal:
            logger.warning(f'LP optimization did not converge (status: {pl.LpStatus[prob.status]}), falling back to deficit strategy')
            return self._aporte_deficit(df, valor_cart, aporte, mes, data)

        logger.info("Optimization solved successfully")
        df["Qtd_comprar"], df["Custo_real"] = 0.0, 0.0
        
        total_cost = 0
        assets_bought = 0
        
        for idx, var in qt_rf.items():
            qtd = var.varValue or 0
            if qtd > 0:
                df.at[idx,"Qtd_comprar"] = qtd
                df.at[idx,"Custo_real"]  = qtd * df.at[idx,"Cotação"]
                total_cost += qtd * df.at[idx,"Cotação"]
                assets_bought += 1
                logger.debug(f"Buying {qtd:.4f} units of {df.at[idx,'Ticker']} (RF) at R$ {df.at[idx,'Cotação']:.2f} = R$ {qtd * df.at[idx,'Cotação']:.2f}")
                
                # Salvar detalhes do aporte se houve compra
                self._salvar_aporte_detalhado(df.loc[idx], qtd, qtd * df.at[idx,"Cotação"], "PO", mes, data, df)
                
        for idx, var in qt_rv.items():
            qtd = int(var.varValue or 0)
            if qtd > 0:
                df.at[idx,"Qtd_comprar"] = qtd
                df.at[idx,"Custo_real"]  = qtd * df.at[idx,"Cotação"]
                total_cost += qtd * df.at[idx,"Cotação"]
                assets_bought += 1
                logger.debug(f"Buying {qtd} shares of {df.at[idx,'Ticker']} (RV) at R$ {df.at[idx,'Cotação']:.2f} = R$ {qtd * df.at[idx,'Cotação']:.2f}")
                
                # Salvar detalhes do aporte se houve compra
                self._salvar_aporte_detalhado(df.loc[idx], qtd, qtd * df.at[idx,"Cotação"], "PO", mes, data, df)

        leftover = aporte - total_cost
        logger.info(f"PO strategy completed: {assets_bought} assets bought, R$ {total_cost:,.2f} invested, R$ {leftover:,.2f} leftover")
        return df, leftover

    # ------------- método para salvar aportes detalhados ---------------
    def _salvar_aporte_detalhado(self, row, qtd_aportada, valor_aportado, estrategia, mes, data, df_cart):
        """Salva os detalhes do aporte para um ativo específico"""
        logger.debug(f"Saving detailed contribution for {row.get('Ticker', 'Unknown')}: {qtd_aportada} units, R$ {valor_aportado:.2f}")
        
        # Calcular % Atual e Variação
        valor_total_carteira = df_cart["Total"].sum()
        pct_atual = (row["Total"] / valor_total_carteira) * 100 if valor_total_carteira > 0 else 0
        pct_ideal = row["% Ideal - Ref."] * 100
        variacao = pct_atual - pct_ideal
        
        aporte_info = {
            'Mes': mes,
            'Data': data,
            'Estrategia': estrategia,
            'Geo': row.get("Geo.", ""),
            'Classe': row.get("Classe", ""),
            'Subclasses': row.get("Subclasses", ""),
            'Setor': row.get("Setor", ""),
            'Ativo': row.get("Ativo", ""),
            'Ticker': row.get("Ticker", ""),
            'Qnt_Total': row.get("Qnt.", 0),
            'Cotacao': row.get("Cotação", 0),
            'Total': row.get("Total", 0),
            'Pct_Atual': pct_atual,
            'Pct_Ideal': pct_ideal,
            'Variacao': variacao,
            'Qnt_Aportado': qtd_aportada,
            'Valor_Aportado': valor_aportado
        }
        
        self.aportes_detalhados.append(aporte_info)

    # ------------- método para obter dataframe de aportes --------------
    def obter_df_aportes(self):
        """Retorna DataFrame com todos os aportes detalhados"""
        logger.debug(f"Retrieving detailed contributions dataframe: {len(self.aportes_detalhados)} records")
        
        if not self.aportes_detalhados:
            logger.warning("No detailed contributions found")
            return pd.DataFrame()
        
        df_aportes = pd.DataFrame(self.aportes_detalhados)
        logger.info(f"Detailed contributions dataframe created with {len(df_aportes)} records")
        return df_aportes

    # ------------- loop principal -----------------------
    def simular(self, meses=24, data_fim_str=None):
        logger.info(f"Starting portfolio simulation for {meses} months")
        logger.debug(f"End date: {data_fim_str if data_fim_str else 'Current date'}")
        
        # Limpar aportes anteriores
        self.aportes_detalhados = []
        logger.debug("Cleared previous detailed contributions")
        
        try:
            dados = self.obter_dados_historicos(meses, data_fim_str)
            logger.info(f"Historical data obtained for {len(dados)} tickers")
        except Exception as e:
            logger.error(f"Error obtaining historical data: {str(e)}")
            raise
        
        cart_d, cart_p = self.df_original.copy(), self.df_original.copy()
        logger.debug("Created deficit and portfolio optimization portfolios")

        dates = pd.date_range(end=datetime.now(), periods=meses, freq="MS")
        logger.debug(f"Simulation dates: {dates[0].strftime('%Y-%m-%d')} to {dates[-1].strftime('%Y-%m-%d')}")
        
        valor_inicial = cart_d["Total"].sum()
        logger.info(f"Initial portfolio value: R$ {valor_inicial:,.2f}")

        aporte_acum, out = 0.0, []

        # --------------------------------------------------------------
        # acumuladores de aportes por classe e por estratégia
        # --------------------------------------------------------------
        aportado_def = {cls: 0.0 for cls in self.classes}
        aportado_po  = {cls: 0.0 for cls in self.classes}
        logger.debug(f"Initialized contribution trackers for classes: {self.classes}")

        logger.info("Starting monthly simulation loop")
        for imes, dt in enumerate(dates, 1):
            logger.info(f"Processing month {imes}/{meses}: {dt.strftime('%Y-%m-%d')}")
            
            # Update prices
            price_updates = 0
            missing_data = 0
            
            for cart in (cart_d, cart_p):
                for idx, r in cart.iterrows():
                    tk = r["Ticker"]
                    if tk not in dados: 
                        missing_data += 1
                        continue
                    s = dados[tk][dados[tk].index <= dt]
                    
                    if s.empty:
                        missing_data += 1
                        continue
                    v_raw = s.iloc[-1]                     # pode ser escalar ou Series
                    # extrai o primeiro elemento se for iterável
                    v = float(np.asarray(v_raw).flatten()[0])
                    cart.at[idx, "Cotação"] = v
                    price_updates += 1
                    
                cart["Total"] = cart["Qnt."] * cart["Cotação"]
            
            logger.debug(f"Price updates: {price_updates}, Missing data: {missing_data}")

            # Monthly contributions
            aporte_acum += self.aporte_mensal
            logger.debug(f"Accumulated contributions: R$ {aporte_acum:,.2f}")
            
            # Apply strategies
            try:
                res_d, sobra_d = self._aporte_deficit(cart_d, cart_d["Total"].sum(), self.aporte_mensal, imes, dt)
                res_p, sobra_p = self._aporte_po(cart_p, cart_p["Total"].sum(), self.aporte_mensal, imes, dt)
                
                logger.debug(f"Strategy results - Deficit leftover: R$ {sobra_d:.2f}, PO leftover: R$ {sobra_p:.2f}")
            except Exception as e:
                logger.error(f"Error applying strategies in month {imes}: {str(e)}")
                raise

            # Update quantities
            cart_d["Qnt."] += res_d["Qtd_comprar"]
            cart_p["Qnt."] += res_p["Qtd_comprar"]
            cart_d["Total"] = cart_d["Qnt."] * cart_d["Cotação"]
            cart_p["Total"] = cart_p["Qnt."] * cart_p["Cotação"]

            # Handle leftovers → SELIC
            for cart, sobra, estrategia in ((cart_d,sobra_d,"Deficit"),(cart_p,sobra_p,"PO")):
                if sobra>0 and (cart["Ticker"]=="SELIC").any():
                    idx = cart[cart["Ticker"]=="SELIC"].index[0]
                    cart.at[idx,"Qnt."]  += sobra
                    cart.at[idx,"Total"] += sobra
                    logger.debug(f"Added R$ {sobra:.2f} leftover to SELIC ({estrategia})")
                    
                    # Registrar sobra como aporte na SELIC
                    if sobra > 0:
                        self._salvar_aporte_detalhado(cart.loc[idx], sobra, sobra, estrategia, imes, dt, cart)

            # Calculate performance metrics
            investido = valor_inicial + aporte_acum
            vt_d, vt_p = cart_d["Total"].sum(), cart_p["Total"].sum()
            rent_def_corr = (vt_d/investido -1)*100
            rent_po_corr = (vt_p/investido -1)*100
            
            logger.debug(f"Month {imes} performance - Deficit: {rent_def_corr:.2f}%, PO: {rent_po_corr:.2f}%")
            
            # ---- drift por classe (usar carteira déficit para referência) ---
            drift = {}
            for cls in self.classes:
                # pesos alvo são os mesmos p/ as duas carteiras
                peso_alvo = cart_d[cart_d["Classe"] == cls]["% Ideal - Ref."].sum()

                peso_def  = cart_d[cart_d["Classe"] == cls]["Total"].sum() / vt_d
                peso_po   = cart_p[cart_p["Classe"] == cls]["Total"].sum() / vt_p

                drift[f"drift_{cls}_def"] = (peso_def - peso_alvo) * 100   # p.p.
                drift[f"drift_{cls}_po"]  = (peso_po  - peso_alvo) * 100

            out.append({
                "mes": imes, "data": dt,
                "investido": investido,
                "valor_def": vt_d, "valor_po": vt_p,
                "deficit_def": (cart_d["% Ideal - Ref."]*vt_d - cart_d["Total"]).clip(lower=0).sum(),
                "deficit_po":  (cart_p["% Ideal - Ref."]*vt_p - cart_p["Total"]).clip(lower=0).sum(),
                "rent_def_corr": rent_def_corr,
                "rent_po_corr":  rent_po_corr,
                # "rent_def_rf"  : rent_def_rf,
                # "rent_po_rf"   : rent_po_rf,
                # "rent_def_rv"  : rent_def_rv,
                # "rent_po_rv"   : rent_po_rv,
                **drift
            })

        return pd.DataFrame(out)