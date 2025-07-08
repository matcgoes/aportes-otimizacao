import time
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import yfinance as yf
import requests
import pulp as pl
import logging
from src.utils import _download_with_retry

class PortfolioSimulator:
    def __init__(self, df_portfolio, valor_aporte_mensal=2500, k_min_po=None, logger=None):
        self.logger = logger if logger else logging.getLogger(__name__)
        self.logger.info('starting backtest...')
        self.df_original = df_portfolio.copy()
        if self.df_original["% Ideal - Ref."].max() > 1:
            self.df_original["% Ideal - Ref."] /= 100.0
        self.aporte_mensal = valor_aporte_mensal
        self.k_min_po = k_min_po
        # ← lista de classes p/ cálculo de drift
        self.classes = sorted(self.df_original["Classe"].unique())
        
        # Nova estrutura para armazenar aportes detalhados
        self.aportes_detalhados = []

    # ------------- mapeamento de tickers -----------------
    def mapear_tickers(self):
        """Apenas ativos negociados via Yahoo; RF especial fica com próprio nome."""
        mapa = {}
        for _, row in self.df_original.iterrows():
            tk, geo = row["Ticker"], row["Geo."]
            if tk in {"SELIC", "FDI", "FRFH", "LC", "CDB",
                    "IPCA", "CDBI", "PRE", "PGBL"}:
                mapa[tk] = None          # marcador especial
            elif geo == "BR":
                mapa[tk] = f"{tk}.SA"
            elif geo == "US":
                mapa[tk] = tk
        return mapa
    
    @staticmethod
    def _selic_fator_mensal(start, end):
        """SGS 4390 Selic diária → fator acumulado em datas 'M' (últ. dia útil do mês)."""
        url = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.4390/dados"
            f"?formato=json&dataInicial={start:%d/%m/%Y}&dataFinal={end:%d/%m/%Y}")
        js  = requests.get(url, timeout=10).json()
        df  = pd.DataFrame(js)
        df["data"]  = pd.to_datetime(df["data"], format="%d/%m/%Y")
        df["valor"] = df["valor"].astype(float) / 100        # diário (fração)
        df["fator"] = (1 + df["valor"]).cumprod()
        fator_m = df.set_index("data").resample("M").last()["fator"]
        return fator_m

    @staticmethod
    def _ipca_fator_mensal(start, end):
        """SGS 433 IPCA % mensal → fator acumulado (base 1)."""
        url = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados"
            f"?formato=json&dataInicial={start:%d/%m/%Y}&dataFinal={end:%d/%m/%Y}")
        # print(url)
        js  = requests.get(url, timeout=10).json()
        df  = pd.DataFrame(js)
        df["data"]  = pd.to_datetime(df["data"], format="%d/%m/%Y") + pd.offsets.MonthEnd(0)
        df["valor"] = df["valor"].astype(float) / 100
        df["fator"] = (1 + df["valor"]).cumprod()
        return df.set_index("data")["fator"]

    @staticmethod
    def _fator_mensal_fixo(start, end, taxa_anual):
        """Retorna série mensal de fator acumulado (base 1.0) para uma taxa 'anual' constante."""
        idx = pd.date_range(start, end, freq="M")
        taxa_mensal = (1 + taxa_anual) ** (1/12) - 1          # ⇐ conversão
        return pd.Series((1 + taxa_mensal) ** np.arange(1, len(idx)+1), index=idx)

    # ------------- históricos ----------------------------
    def obter_dados_historicos(self, meses, data_fim_str=None):
        if data_fim_str:
            end_date = datetime.strptime(data_fim_str, '%Y-%m-%d')
        else:
            end_date = datetime.now()
       
        start_date = end_date - timedelta(days=meses*30)

        dados, mapa = {}, self.mapear_tickers()

        usd = _download_with_retry("USDBRL=X", start_date, end_date)["Adj Close"]
        if usd.empty:
            raise RuntimeError("Falha USD/BRL")

        print("Coletando séries…")
        for tk, sym in mapa.items():
            # ─── Renda-fixa e índices ────────────────────────────────
            if tk in {"SELIC", "FDI", "FRFH", "LC", "CDB"}:
                dados[tk] = self._selic_fator_mensal(start_date, end_date)
                continue
            if tk in {"IPCA", "CDBI"}:
                dados[tk] = self._ipca_fator_mensal(start_date, end_date)
                continue
            if tk == "PRE":
                dados[tk] = self._fator_mensal_fixo(start_date, end_date, 0.09)   # 9 % a.a.
                continue
            if tk == "PGBL":
                dados[tk] = self._fator_mensal_fixo(start_date, end_date, 0.07)   # 7 % a.a.
                continue

            # ─── Ações / ETFs ───────────────────────────────────────
            dfp = _download_with_retry(sym, start_date, end_date)
            if dfp.empty:
                print(f"Sem dados {tk}")
                continue
            serie_m = dfp["Adj Close"].resample("M").last()
            if self.df_original.loc[self.df_original["Ticker"] == tk, "Geo."].iat[0] == "US":
                usd_m = usd.resample("M").last()
                serie_m.values[:] = serie_m.to_numpy()*usd_m.to_numpy()

            dados[tk] = serie_m

        return dados

    # ------------- estratégia 1 -------------------------
    def _aporte_deficit(self, df, valor_cart, aporte, mes, data):
        # df = df[df["Cotação"].notna() & np.isfinite(df["Cotação"])].copy()
        df = df.copy()
        df["Valor Ideal"] = df["% Ideal - Ref."] * (valor_cart + aporte)
        df["deficit"] = (df["Valor Ideal"] - df["Total"]).clip(lower=0)
        
        if df["deficit"].sum() == 0:
            return df, aporte
            
        df["aporte_sugerido"] = df["deficit"] / df["deficit"].sum() * aporte
        df["Qtd_comprar"], df["Custo_real"] = 0.0, 0.0
        
        for i, r in df.iterrows():
            qtd = (r["aporte_sugerido"] / r["Cotação"]) if (r["Classe"] == "RF" and r["Ticker"] != "IMAB11") \
                  else np.floor(r["aporte_sugerido"] / r["Cotação"])
            df.at[i, "Qtd_comprar"] = qtd
            df.at[i, "Custo_real"]  = qtd * r["Cotação"]
            
            # Salvar detalhes do aporte se houve compra
            if qtd > 0:
                self._salvar_aporte_detalhado(r, qtd, qtd * r["Cotação"], "Deficit", mes, data, df)

        return df, aporte - df["Custo_real"].sum()

    # ------------- estratégia 2 -------------------------
    def _aporte_po(self, df, valor_cart, aporte, mes, data):
        # df = df[df["Cotação"].notna() & np.isfinite(df["Cotação"])].copy()
        df = df.copy()
        if df.empty:
            return self._aporte_deficit(df, valor_cart, aporte, mes, data)

        df["Valor Ideal"] = df["% Ideal - Ref."] * (valor_cart + aporte)
        df["deficit"] = (df["Valor Ideal"] - df["Total"]).clip(lower=0)
        prob, qt_rf, qt_rv, gap = pl.LpProblem("PO", pl.LpMinimize), {}, {}, {}
        sel = {}                                    # só usado se houver limite

        for idx, r in df.iterrows():
            nome = str(r["Ativo"]).replace(" ", "_").replace(".", "_")

            # ----- variável de seleção (se houver limite K) -------------
            if self.k_min_po:                       # None/0 = não cria
                sel[idx] = pl.LpVariable(f"SEL_{nome}", cat="Binary")

            # ----- quantidade a comprar ---------------------------------
            if r["Classe"] == "RF" and r["Ticker"] != "IMAB11":
                qt_rf[idx] = pl.LpVariable(f"RF_{nome}", lowBound=0)
            else:
                qt_rv[idx] = pl.LpVariable(f"RV_{nome}", lowBound=0, cat="Integer")

            gap[idx] = pl.LpVariable(f"GAP_{nome}", lowBound=0)

        # -------- objetivo: minimizar gaps ------------------------------
        prob += pl.lpSum(gap.values())
        # -------- restrição de orçamento --------------------------------
        prob += (
            pl.lpSum(df.loc[i, "Cotação"] * qt_rf[i] for i in qt_rf)
          + pl.lpSum(df.loc[i, "Cotação"] * qt_rv[i] for i in qt_rv)
        ) <= aporte

        # -------- cardinalidade (se houver K) ---------------------------
        if self.k_min_po:
            # prob += pl.lpSum(sel.values()) <= self.k_max_po
            prob += pl.lpSum(sel.values()) >= self.k_min_po
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

        for idx in df.index:
            preco = df.at[idx, "Cotação"]
            compra = preco * (qt_rf[idx] if idx in qt_rf else qt_rv[idx])
            prob += gap[idx] >= df.at[idx,"deficit"] - compra

        try:
            # pl.LpSolverDefault.msg = 1
            prob.solve(pl.PULP_CBC_CMD(msg=0))
        except pl.PulpError:
            # pl.LpSolverDefault.msg = 1
            # prob.writeLP("model.lp")
            prob.solve()

        if prob.status != pl.LpStatusOptimal:
            print(f'Solução com LP não convergiu')
            return self._aporte_deficit(df, valor_cart, aporte, mes, data)

        df["Qtd_comprar"], df["Custo_real"] = 0.0, 0.0
        
        for idx, var in qt_rf.items():
            qtd = var.varValue or 0
            df.at[idx,"Qtd_comprar"] = qtd
            df.at[idx,"Custo_real"]  = qtd * df.at[idx,"Cotação"]
            
            # Salvar detalhes do aporte se houve compra
            if qtd > 0:
                self._salvar_aporte_detalhado(df.loc[idx], qtd, qtd * df.at[idx,"Cotação"], "PO", mes, data, df)
                
        for idx, var in qt_rv.items():
            qtd = int(var.varValue or 0)
            df.at[idx,"Qtd_comprar"] = qtd
            df.at[idx,"Custo_real"]  = qtd * df.at[idx,"Cotação"]
            
            # Salvar detalhes do aporte se houve compra
            if qtd > 0:
                self._salvar_aporte_detalhado(df.loc[idx], qtd, qtd * df.at[idx,"Cotação"], "PO", mes, data, df)

        return df, aporte - df["Custo_real"].sum()

    # ------------- método para salvar aportes detalhados ---------------
    def _salvar_aporte_detalhado(self, row, qtd_aportada, valor_aportado, estrategia, mes, data, df_cart):
        """Salva os detalhes do aporte para um ativo específico"""
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
        if not self.aportes_detalhados:
            return pd.DataFrame()
        
        df_aportes = pd.DataFrame(self.aportes_detalhados)
    
        return df_aportes

    # ------------- loop principal -----------------------
    def simular(self, meses=24, data_fim_str=None):
        # Limpar aportes anteriores
        self.aportes_detalhados = []
        
        dados = self.obter_dados_historicos(meses, data_fim_str)
        cart_d, cart_p = self.df_original.copy(), self.df_original.copy()

        dates = pd.date_range(end=datetime.now(), periods=meses, freq="MS")
        valor_inicial = cart_d["Total"].sum()
        # valor_inicial_classe = (
        #     cart_d.groupby("Classe")["Total"].sum().to_dict()
        # )

        aporte_acum, out = 0.0, []

        # --------------------------------------------------------------
        # acumuladores de aportes por classe e por estratégia
        # --------------------------------------------------------------
        aportado_def = {cls: 0.0 for cls in self.classes}
        aportado_po  = {cls: 0.0 for cls in self.classes}

        for imes, dt in enumerate(dates, 1):
            # print(imes)
            # preços atualizados
            for cart in (cart_d, cart_p):
                for idx, r in cart.iterrows():
                    tk = r["Ticker"]
                    if tk not in dados: continue
                    s = dados[tk][dados[tk].index <= dt]
                    
                    if s.empty:
                        continue
                    v_raw = s.iloc[-1]                     # pode ser escalar ou Series
                    # extrai o primeiro elemento se for iterável
                    v = float(np.asarray(v_raw).flatten()[0])
                    cart.at[idx, "Cotação"] = v
                cart["Total"] = cart["Qnt."] * cart["Cotação"]

            # aportes (passando mes e data para tracking)
            aporte_acum += self.aporte_mensal
            res_d, sobra_d = self._aporte_deficit(cart_d, cart_d["Total"].sum(), self.aporte_mensal, imes, dt)
            res_p, sobra_p = self._aporte_po(cart_p, cart_p["Total"].sum(), self.aporte_mensal, imes, dt)

            cart_d["Qnt."] += res_d["Qtd_comprar"]; cart_p["Qnt."] += res_p["Qtd_comprar"]
            cart_d["Total"] = cart_d["Qnt."] * cart_d["Cotação"]
            cart_p["Total"] = cart_p["Qnt."] * cart_p["Cotação"]

            # sobras → SELIC
            for cart, sobra, estrategia in ((cart_d,sobra_d,"Deficit"),(cart_p,sobra_p,"PO")):
                if sobra>0 and (cart["Ticker"]=="SELIC").any():
                    idx = cart[cart["Ticker"]=="SELIC"].index[0]
                    cart.at[idx,"Qnt."]  += sobra
                    cart.at[idx,"Total"] += sobra
                    
                    # Registrar sobra como aporte na SELIC
                    if sobra > 0:
                        self._salvar_aporte_detalhado(cart.loc[idx], sobra, sobra, estrategia, imes, dt, cart)

            investido = valor_inicial + aporte_acum
            vt_d, vt_p = cart_d["Total"].sum(), cart_p["Total"].sum()
            rent_def_corr = (vt_d/investido -1)*100
            rent_po_corr = (vt_p/investido -1)*100
            
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