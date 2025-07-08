import pandas as pd
import numpy as np
import pulp as pl
import logging

def aporte_inicial(df, valor_carteira, valor_aporte):

    logger = logging.getLogger('app_main_logger')

    logger.info('Aporte inicial Rebalanceamento...')

    df = df.copy()

    df['Valor Ideal'] = df['% Ideal - Ref.']*(valor_carteira+valor_aporte)
    df['deficit'] = (df['Valor Ideal'] - df['Total']).clip(lower=0)

    df_grp = df.groupby(['Geo.', 'Classe', 'Subclasses', 'Ativo', 'Ticker'], as_index=False).agg(deficit = ('deficit', 'sum'))
    total_deficit = df_grp['deficit'].sum()
    df_grp['aporte'] = (df_grp['deficit']/total_deficit)*valor_aporte

    # Cruza para pegar Cotação e quantidade
    df_grp = pd.merge(
        df_grp,
        df[['Geo.', 'Classe', 'Subclasses', 'Ativo', 'Ticker', 'Qnt.','Cotação']],
        how='left',
        on=['Geo.', 'Classe', 'Subclasses', 'Ativo', 'Ticker']
    )

    # Calculo da quantidade necessaria, segundo valor do aporte
    # Para Renda Fixa de cotação = 1, calcular normalmente
    # Para Renda Variável com cotação maior que 1, calcular quantidade inteira possível

    df_grp['Qtd_nec'] = 0.0
    df_grp['Custo_real'] = 0.0

    df_grp.loc[(df_grp['Classe']=='RF') & (df_grp['Ticker']!='IMAB11'), 'Qtd_nec'] = df_grp['aporte'] / df_grp['Cotação']
    df_grp.loc[(df_grp['Classe']=='RF') & (df_grp['Ticker']!='IMAB11'), 'Custo_real'] = df_grp['aporte']

    df_grp.loc[(df_grp['Classe']!='RF') | (df_grp['Ticker']=='IMAB11'), 'Qtd_nec'] = np.floor(df_grp['aporte'] / df_grp['Cotação'])
    df_grp.loc[(df_grp['Classe']!='RF') | (df_grp['Ticker']=='IMAB11'), 'Custo_real'] = df_grp['Qtd_nec']*df_grp['Cotação']

    return df_grp

def calcula_sobra(df, valor_aporte):

    custo_total = df['Custo_real'].sum()
    vlr_sobra = valor_aporte - custo_total

    return np.max([0,vlr_sobra])

def menor_preco_viavel(df):

    elegiveis = df[(df['Classe'] != 'RF') & (df['deficit'] > 0) & (df['Cotação'] > 0)]

    return elegiveis['Cotação'].min() if not elegiveis.empty else np.inf

def redistribui_sobra(df, vlr_sobra):

    df = df.copy()

    while vlr_sobra >= menor_preco_viavel(df) - 1e-6:

        alvo = df[(df['Classe'] != 'RF') & (df['deficit'] > 0)].sort_values('deficit', ascending=False).iloc[0]
        preco = alvo['Cotação']
        ativo = alvo['Ativo']
        deficit = alvo['deficit']
        if vlr_sobra < preco:
            break

        df.loc[df['Ativo']==ativo, 'Qtd_nec'] +=1
        df.loc[df['Ativo']==ativo, 'Custo_real'] +=preco

        vlr_sobra -= preco

        df.loc[df['Ativo']==ativo, 'deficit'] = max(0,deficit-preco)

    return df, vlr_sobra

def otimiza_aporte(df, valor_carteira=None, valor_aporte=2500):

    if valor_carteira is None:
        valor_carteira = df['Total'].sum()
        
    # valor_carteira = df['Total'].sum()

    base = aporte_inicial(df, valor_carteira, valor_aporte)
    if base.empty:
        return base, valor_aporte

    vlr_sobra = calcula_sobra(base, valor_aporte)

    for _ in range(30):
        menor = menor_preco_viavel(base)
        if vlr_sobra < menor - 1e-6:
            break
        base, vlr_sobra = redistribui_sobra(base, vlr_sobra)

    return base, vlr_sobra

def exibir_resultado_formatado(df_resultado, sobra, valor_aporte):
    """Exibe resultado de forma padronizada e formatada."""

    resultado = df_resultado[df_resultado['Custo_real'] > 0].copy()
    resultado = resultado.sort_values('Custo_real', ascending=False).reset_index(drop=True)
    resultado = resultado[['Geo.', 'Classe', 'Subclasses', 'Ativo', 'Ticker', 
                          'Cotação', 'Qtd_nec', 'Custo_real']]
    
    custo_total = resultado['Custo_real'].sum()


    print(f"\nRESULTADO - REBALANCEAMENTO")
    print("="*60)
    print(resultado)
    
    print('\n' + '='*60)
    print('RESUMO FINANCEIRO:')
    print('='*60)
    print(f'Orçamento Total:    R$ {valor_aporte:,.2f}')
    print(f'Custo Total:        R$ {custo_total:,.2f}')
    print(f'Sobra:              R$ {sobra:,.2f}')
    print(f'Utilização:         {(custo_total/valor_aporte)*100:.1f}%')
    
    if sobra > 0:
        print(f'\nSobra de R$ {sobra:,.2f} vai para SELIC')


def criar_variaveis_lp(df):
    '''
    Cria as variáveis de decisão para RF, RV e gaps.
    '''
    df = df.copy()

    qt_rf = {} # Dicionario para armazenar as variaveis de RF (continua) {id: 'nome_var_ativo'}
    qt_rv = {} # Dicionario para armazenar as variaveis de RV (inteiro) {id: 'nome_var_ativo'}
    gap = {} # Gaps residuais, ou seja, o que sobrou apos a compra (deficit - aporte)
    sel = {}

    for idx, row in df.iterrows():
        ativo_i = row['Ativo'].replace(' ','_')
        sel[idx] = pl.LpVariable(f'SEL_{ativo_i}', cat='Binary')
        preco_i = row['Cotação']
        deficit_i = row['deficit']

        if row['Classe']=='RF' and row['Ticker'] != 'IMAB11':
            qt_rf[idx] = pl.LpVariable(f'RF_{ativo_i}', lowBound=0) # Atribui variaveis RF
        else:
            qt_rv[idx] = pl.LpVariable(f'RV_{ativo_i}', lowBound=0, cat='Integer') # Atribui variaveis RV
        
        gap[idx] = pl.LpVariable(f'GAP_{ativo_i}', lowBound=0)

    return qt_rf, qt_rv, gap, sel

def adicionar_restricoes_lp(prob, df, qt_rf, qt_rv, gap, sel,
                            valor_aporte, k_min):
    '''
    Adiciona todas as restrições ao problema de otimização.
    '''
    df = df.copy()
    # Calculo de gastos da cotação x qtd (xi)
    gasto_rf = pl.lpSum(df.loc[i,'Cotação']*qt_rf[i] for i in qt_rf)
    gasto_rv = pl.lpSum(df.loc[i,'Cotação']*qt_rv[i] for i in qt_rv)

    # Adiciona Restrição
    prob += (gasto_rf + gasto_rv) <= valor_aporte

    # -------- cardinalidade (se solicitada) -----------
    if k_min is not None:
        prob += pl.lpSum(sel.values()) >= k_min

        for idx, row in df.iterrows():
            preco_i = row['Cotação']
            deficit_i = row['deficit']
            max_qtd = valor_aporte / preco_i      # Big-M natural
            min_qtd = 1 if idx in qt_rv else (1/preco_i)

            if idx in qt_rf:
                prob += qt_rf[idx] >= min_qtd * sel[idx]
                prob += qt_rf[idx] <= max_qtd * sel[idx]
            else:
                prob += qt_rv[idx] >= min_qtd * sel[idx]
                prob += qt_rv[idx] <= max_qtd * sel[idx]

    for idx, row in df.iterrows():
        preco_i = row['Cotação']
        deficit_i = row['deficit']        

        if idx in qt_rf:            
            compra_valor = preco_i * qt_rf[idx]
        else:
            compra_valor = preco_i * qt_rv[idx]

        # Restrição do gap
        prob += gap[idx] >= deficit_i - compra_valor
    
def definir_objetivo_lp(prob, gap):
    '''
    Define a função objetivo: minimizar soma dos gaps.
    '''
    prob += pl.lpSum(gap.values())

def extrair_resultados_lp(df, qt_rf, qt_rv, valor_aporte, show=True):
    '''
    Extrai os resultados da otimização e calcula custo total.
    '''
    df = df.copy()

    resultados = []
    custo_total = 0

    # Processar resultados RF
    for idx in qt_rf:
        qtd_comprada = qt_rf[idx].varValue
        if qtd_comprada > 0:
            resultado = {
                'Geo.': df.loc[idx, 'Geo.'],
                'Ticker': df.loc[idx, 'Ticker'],
                'Ativo': df.loc[idx, 'Ativo'],
                'Classe': df.loc[idx, 'Classe'],
                'Sublasses': df.loc[idx, 'Subclasses'],
                'Cotação': df.loc[idx, 'Cotação'],
                'Qtd_Comprada': qtd_comprada,
                'Valor_Compra': qtd_comprada * df.loc[idx, 'Cotação']
            }
            resultados.append(resultado)
            custo_total += resultado['Valor_Compra']
    
    # Processar resultados RV
    for idx in qt_rv:
        qtd_comprada = qt_rv[idx].varValue
        if qtd_comprada > 0:
            resultado = {
                'Geo.': df.loc[idx, 'Geo.'],
                'Ticker': df.loc[idx, 'Ticker'],
                'Ativo': df.loc[idx, 'Ativo'],
                'Classe': df.loc[idx, 'Classe'],
                'Sublasses': df.loc[idx, 'Subclasses'],
                'Cotação': df.loc[idx, 'Cotação'],
                'Qtd_Comprada': int(qtd_comprada),  # Inteiro para RV
                'Valor_Compra': qtd_comprada * df.loc[idx, 'Cotação']
            }
            
            resultados.append(resultado)
            custo_total += resultado['Valor_Compra']

    sobra = valor_aporte - custo_total

    if resultados:
        df_resultado = pd.DataFrame(resultados)
        df_resultado = df_resultado.sort_values('Valor_Compra', ascending=False)

        if show:
            print(f"\nRESULTADO - PESQUISA OPERACIONAL")
            print("="*60)
            print(df_resultado)
            print('\n' + '=' * 60)
            print('RESUMO FINANCEIRO:')
            print('=' * 60)
            print(f'Orçamento Total:    R$ {valor_aporte:,.2f}')
            print(f'Custo Total:        R$ {custo_total:,.2f}')
            print(f'Sobra:              R$ {sobra:,.2f}')
            print(f'Utilização:         {(custo_total/valor_aporte)*100:.1f}%')
            
            if sobra > 0:
                print(f'\nSobra de R$ {sobra:,.2f} vai para SELIC')
        return df_resultado
    else:
        return None
    
def otimizar_aporte_lp(df, valor_aporte,  valor_carteira=None, k_min=None):
    df = df.copy()

    df['Valor Ideal'] = df['% Ideal - Ref.'] * (valor_carteira  + valor_aporte)
    df['deficit'] = (df['Valor Ideal'] - df['Total']).clip(lower=0)
    df['Qtd_nec'] = 0.0
    df['Custo_real'] = 0.0

    prob = pl.LpProblem('Otimizacao_Aporte', pl.LpMinimize)
    qt_rf, qt_rv, gap, sel = criar_variaveis_lp(df)
    adicionar_restricoes_lp(prob, df, qt_rf, qt_rv, gap, sel,
                            valor_aporte, k_min)
    definir_objetivo_lp(prob, gap)
    # prob.solve()
    prob.solve(pl.PULP_CBC_CMD(msg=0))
    if pl.LpStatus[prob.status] == "Optimal":
        extrair_resultados_lp(df, qt_rf, qt_rv, valor_aporte, show=True)
        return True
    else:
        return False