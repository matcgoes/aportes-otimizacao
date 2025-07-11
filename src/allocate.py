import pandas as pd
import numpy as np
import pulp as pl
import logging

logger = logging.getLogger(__name__)

def aporte_inicial(df, valor_carteira, valor_aporte):

    logger.info('Initial Assets has been started...')
    logger.debug(f'Portfolio value: R$ {valor_carteira:,.2f}, Contribution: R$ {valor_aporte:,.2f}')

    df = df.copy()
    logger.debug(f'Processing {len(df)} assets')

    df['Valor Ideal'] = df['% Ideal - Ref.']*(valor_carteira+valor_aporte)
    df['deficit'] = (df['Valor Ideal'] - df['Total']).clip(lower=0)
    logger.debug(f'Total deficit calculated: R$ {df["deficit"].sum():,.2f}')

    df_grp = df.groupby(['Geo.', 'Classe', 'Subclasses', 'Ativo', 'Ticker'], as_index=False).agg(deficit = ('deficit', 'sum'))
    total_deficit = df_grp['deficit'].sum()
    logger.debug(f'Assets grouped: {len(df_grp)} unique assets with total deficit: R$ {total_deficit:,.2f}')
    
    df_grp['aporte'] = (df_grp['deficit']/total_deficit)*valor_aporte
    logger.debug(f'Proportional allocation calculated for {len(df_grp)} assets')

    # Cruza para pegar Cotação e quantidade
    df_grp = pd.merge(
        df_grp,
        df[['Geo.', 'Classe', 'Subclasses', 'Ativo', 'Ticker', 'Qnt.','Cotação']],
        how='left',
        on=['Geo.', 'Classe', 'Subclasses', 'Ativo', 'Ticker']
    )
    logger.debug('Price and quantity data merged successfully')

    # Calculo da quantidade necessaria, segundo valor do aporte
    # Para Renda Fixa de cotação = 1, calcular normalmente
    # Para Renda Variável com cotação maior que 1, calcular quantidade inteira possível

    df_grp['Qtd_nec'] = 0.0
    df_grp['Custo_real'] = 0.0

    rf_assets = (df_grp['Classe']=='RF') & (df_grp['Ticker']!='IMAB11')
    rv_assets = (df_grp['Classe']!='RF') | (df_grp['Ticker']=='IMAB11')
    
    logger.debug(f'Fixed income assets (RF): {rf_assets.sum()}')
    logger.debug(f'Variable income assets (RV): {rv_assets.sum()}')

    df_grp.loc[rf_assets, 'Qtd_nec'] = df_grp['aporte'] / df_grp['Cotação']
    df_grp.loc[rf_assets, 'Custo_real'] = df_grp['aporte']

    df_grp.loc[rv_assets, 'Qtd_nec'] = np.floor(df_grp['aporte'] / df_grp['Cotação'])
    df_grp.loc[rv_assets, 'Custo_real'] = df_grp['Qtd_nec']*df_grp['Cotação']

    logger.debug(f'Quantities and real costs calculated. Total real cost: R$ {df_grp["Custo_real"].sum():,.2f}')
    logger.info('Initial Assets calculation completed successfully')

    return df_grp

def calcula_sobra(df, valor_aporte):

    logger.info('Computing remaining values...')
    logger.debug(f'Contribution amount: R$ {valor_aporte:,.2f}')

    custo_total = df['Custo_real'].sum()
    vlr_sobra = valor_aporte - custo_total
    
    logger.debug(f'Total cost: R$ {custo_total:,.2f}')
    logger.debug(f'Remaining value: R$ {vlr_sobra:,.2f}')
    
    final_sobra = np.max([0,vlr_sobra])
    logger.info(f'Final remaining value calculated: R$ {final_sobra:,.2f}')

    return final_sobra

def menor_preco_viavel(df):

    # logger.info('Computing least feasible price...')

    elegiveis = df[(df['Classe'] != 'RF') & (df['deficit'] > 0) & (df['Cotação'] > 0)]
    logger.debug(f'Eligible assets for redistribution: {len(elegiveis)}')

    if not elegiveis.empty:
        min_price = elegiveis['Cotação'].min()
        logger.debug(f'Minimum viable price found: R$ {min_price:,.2f}')
        return min_price
    else:
        logger.debug('No eligible assets found, returning infinity')
        return np.inf

def redistribui_sobra(df, vlr_sobra):

    # logger.info('Allocating remaining trades...')
    logger.debug(f'Value to redistribute: R$ {vlr_sobra:,.2f}')

    df = df.copy()
    iterations = 0
    initial_sobra = vlr_sobra

    while vlr_sobra >= menor_preco_viavel(df) - 1e-6:
        iterations += 1
        logger.debug(f'Redistribution iteration {iterations}')

        alvo = df[(df['Classe'] != 'RF') & (df['deficit'] > 0)].sort_values('deficit', ascending=False).iloc[0]
        preco = alvo['Cotação']
        ativo = alvo['Ativo']
        deficit = alvo['deficit']
        
        logger.debug(f'Target asset: {ativo}, Price: R$ {preco:,.2f}, Deficit: R$ {deficit:,.2f}')
        
        if vlr_sobra < preco:
            logger.debug(f'Insufficient remaining value (R$ {vlr_sobra:,.2f}) for asset price (R$ {preco:,.2f})')
            break

        df.loc[df['Ativo']==ativo, 'Qtd_nec'] +=1
        df.loc[df['Ativo']==ativo, 'Custo_real'] +=preco

        vlr_sobra -= preco
        logger.debug(f'Purchased 1 unit of {ativo}. Remaining value: R$ {vlr_sobra:,.2f}')

        df.loc[df['Ativo']==ativo, 'deficit'] = max(0,deficit-preco)

    # logger.info(f'Redistribution completed after {iterations} iterations')
    logger.debug(f'Value redistributed: R$ {initial_sobra - vlr_sobra:,.2f}')
    logger.debug(f'Final remaining value: R$ {vlr_sobra:,.2f}')

    return df, vlr_sobra

def otimiza_aporte(df, valor_carteira=None, valor_aporte=2500):

    logger.info('Optimizing Allocation...')
    logger.debug(f'Allocation Value: R$ {valor_aporte:,.2f}')

    if valor_carteira is None:
        valor_carteira = df['Total'].sum()
        logger.debug(f'Portfolio value calculated from data: R$ {valor_carteira:,.2f}')
    else:
        logger.debug(f'Portfolio value provided: R$ {valor_carteira:,.2f}')
        
    # valor_carteira = df['Total'].sum()

    base = aporte_inicial(df, valor_carteira, valor_aporte)
    if base.empty:
        logger.warning('No assets to process - returning empty result')
        return base, valor_aporte

    vlr_sobra = calcula_sobra(base, valor_aporte)
    logger.debug(f'Initial remaining value: R$ {vlr_sobra:,.2f}')

    for iteration in range(30):
        menor = menor_preco_viavel(base)
        logger.debug(f'Optimization iteration {iteration + 1}/30, Min viable price: R$ {menor:,.2f}')
        
        if vlr_sobra < menor - 1e-6:
            logger.debug(f'Optimization stopped: remaining value (R$ {vlr_sobra:,.2f}) < min price (R$ {menor:,.2f})')
            break
        base, vlr_sobra = redistribui_sobra(base, vlr_sobra)

    logger.info(f'Optimization completed after {iteration + 1} iterations')
    logger.debug(f'Final remaining value: R$ {vlr_sobra:,.2f}')

    return base, vlr_sobra

def exibir_resultado_formatado(df_resultado, sobra, valor_aporte):
    """Exibe resultado de forma padronizada e formatada."""

    logger.info('Displaying Rebalancing results for final user')
    logger.debug(f'Processing {len(df_resultado)} assets for display')

    resultado = df_resultado[df_resultado['Custo_real'] > 0].copy()
    logger.debug(f'Assets with actual purchases: {len(resultado)}')
    
    resultado = resultado.sort_values('Custo_real', ascending=False).reset_index(drop=True)
    resultado = resultado[['Geo.', 'Classe', 'Subclasses', 'Ativo', 'Ticker', 
                          'Cotação', 'Qtd_nec', 'Custo_real']]
    
    custo_total = resultado['Custo_real'].sum()
    utilizacao = (custo_total/valor_aporte)*100

    logger.debug(f'Total cost: R$ {custo_total:,.2f}')
    logger.debug(f'Remaining: R$ {sobra:,.2f}')
    logger.debug(f'Utilization: {utilizacao:.1f}%')
    logger.debug(f'Result data:\n{resultado}')

    print(f"\nRESULTADO - REBALANCEAMENTO")
    print("="*60)
    print(resultado)
    
    print('\n' + '='*60)
    print('RESUMO FINANCEIRO:')
    print('='*60)
    print(f'Orçamento Total:    R$ {valor_aporte:,.2f}')
    print(f'Custo Total:        R$ {custo_total:,.2f}')
    print(f'Sobra:              R$ {sobra:,.2f}')
    print(f'Utilização:         {utilizacao:.1f}%')
    
    if sobra > 0:
        print(f'\nSobra de R$ {sobra:,.2f} vai para SELIC')
        logger.info(f'Remaining R$ {sobra:,.2f} will go to SELIC')

    logger.info('Rebalancing results displayed successfully')

def criar_variaveis_lp(df):
    '''
    Cria as variáveis de decisão para RF, RV e gaps.
    '''
    df = df.copy()

    logger.info('Creating variables for Linear Programming Operation')
    logger.debug(f'Processing {len(df)} assets for LP variables')

    qt_rf = {} # Dicionario para armazenar as variaveis de RF (continua) {id: 'nome_var_ativo'}
    qt_rv = {} # Dicionario para armazenar as variaveis de RV (inteiro) {id: 'nome_var_ativo'}
    gap = {} # Gaps residuais, ou seja, o que sobrou apos a compra (deficit - aporte)
    sel = {}

    rf_count = 0
    rv_count = 0

    for idx, row in df.iterrows():
        ativo_i = row['Ativo'].replace(' ','_')
        sel[idx] = pl.LpVariable(f'SEL_{ativo_i}', cat='Binary')
        preco_i = row['Cotação']
        deficit_i = row['deficit']
        
        logger.debug(f'Creating variables for asset: {row["Ativo"]} (Class: {row["Classe"]}, Price: R$ {preco_i:,.2f}, Deficit: R$ {deficit_i:,.2f})')

        if row['Classe']=='RF' and row['Ticker'] != 'IMAB11':
            qt_rf[idx] = pl.LpVariable(f'RF_{ativo_i}', lowBound=0) # Atribui variaveis RF
            rf_count += 1
        else:
            qt_rv[idx] = pl.LpVariable(f'RV_{ativo_i}', lowBound=0, cat='Integer') # Atribui variaveis RV
            rv_count += 1
        
        gap[idx] = pl.LpVariable(f'GAP_{ativo_i}', lowBound=0)

    logger.debug(f'Created {rf_count} RF variables, {rv_count} RV variables, {len(gap)} gap variables, {len(sel)} selection variables')
    logger.info('LP variables created successfully')

    return qt_rf, qt_rv, gap, sel

def adicionar_restricoes_lp(prob, df, qt_rf, qt_rv, gap, sel,
                            valor_aporte, k_min):
    '''
    Adiciona todas as restrições ao problema de otimização.
    '''

    logger.info('Adding restraints for problem optimization...')
    logger.debug(f'Budget constraint: R$ {valor_aporte:,.2f}')
    logger.debug(f'Minimum cardinality (k_min): {k_min}')

    df = df.copy()
    # Calculo de gastos da cotação x qtd (xi)
    gasto_rf = pl.lpSum(df.loc[i,'Cotação']*qt_rf[i] for i in qt_rf)
    gasto_rv = pl.lpSum(df.loc[i,'Cotação']*qt_rv[i] for i in qt_rv)

    # Adiciona Restrição
    prob += (gasto_rf + gasto_rv) <= valor_aporte
    logger.debug('Budget constraint added')

    # -------- cardinalidade (se solicitada) -----------
    if k_min is not None:
        prob += pl.lpSum(sel.values()) >= k_min
        logger.debug(f'Cardinality constraint added: minimum {k_min} assets')

        constraints_added = 0
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
            
            constraints_added += 2
        
        logger.debug(f'Added {constraints_added} cardinality-related constraints')

    gap_constraints = 0
    for idx, row in df.iterrows():
        preco_i = row['Cotação']
        deficit_i = row['deficit']        

        if idx in qt_rf:            
            compra_valor = preco_i * qt_rf[idx]
        else:
            compra_valor = preco_i * qt_rv[idx]

        # Restrição do gap
        prob += gap[idx] >= deficit_i - compra_valor
        gap_constraints += 1
    
    logger.debug(f'Added {gap_constraints} gap constraints')
    logger.info('All constraints added successfully')

def definir_objetivo_lp(prob, gap):
    '''
    Define a função objetivo: minimizar soma dos gaps.
    '''
    logger.info('Defining objective problem optimization...')
    logger.debug(f'Objective: minimize sum of {len(gap)} gap variables')
    
    prob += pl.lpSum(gap.values())
    logger.debug('Objective function set to minimize total gaps')

def extrair_resultados_lp(df, qt_rf, qt_rv, valor_aporte, show=True):
    '''
    Extrai os resultados da otimização e calcula custo total.
    '''

    logger.info('Extracting optimized values...')
    logger.debug(f'Processing {len(qt_rf)} RF variables and {len(qt_rv)} RV variables')

    df = df.copy()

    resultados = []
    custo_total = 0
    rf_purchases = 0
    rv_purchases = 0

    # Processar resultados RF
    for idx in qt_rf:
        qtd_comprada = qt_rf[idx].varValue
        if qtd_comprada > 0:
            valor_compra = qtd_comprada * df.loc[idx, 'Cotação']
            logger.debug(f'RF Purchase: {df.loc[idx, "Ativo"]} - Qty: {qtd_comprada:.4f}, Value: R$ {valor_compra:,.2f}')
            
            resultado = {
                'Geo.': df.loc[idx, 'Geo.'],
                'Ticker': df.loc[idx, 'Ticker'],
                'Ativo': df.loc[idx, 'Ativo'],
                'Classe': df.loc[idx, 'Classe'],
                'Sublasses': df.loc[idx, 'Subclasses'],
                'Cotação': df.loc[idx, 'Cotação'],
                'Qtd_Comprada': qtd_comprada,
                'Valor_Compra': valor_compra
            }
            resultados.append(resultado)
            custo_total += resultado['Valor_Compra']
            rf_purchases += 1
    
    # Processar resultados RV
    for idx in qt_rv:
        qtd_comprada = qt_rv[idx].varValue
        if qtd_comprada > 0:
            valor_compra = qtd_comprada * df.loc[idx, 'Cotação']
            logger.debug(f'RV Purchase: {df.loc[idx, "Ativo"]} - Qty: {int(qtd_comprada)}, Value: R$ {valor_compra:,.2f}')
            
            resultado = {
                'Geo.': df.loc[idx, 'Geo.'],
                'Ticker': df.loc[idx, 'Ticker'],
                'Ativo': df.loc[idx, 'Ativo'],
                'Classe': df.loc[idx, 'Classe'],
                'Sublasses': df.loc[idx, 'Subclasses'],
                'Cotação': df.loc[idx, 'Cotação'],
                'Qtd_Comprada': int(qtd_comprada),  # Inteiro para RV
                'Valor_Compra': valor_compra
            }
            
            resultados.append(resultado)
            custo_total += resultado['Valor_Compra']
            rv_purchases += 1

    sobra = valor_aporte - custo_total
    utilizacao = (custo_total/valor_aporte)*100

    logger.debug(f'RF purchases: {rf_purchases}, RV purchases: {rv_purchases}')
    logger.debug(f'Total cost: R$ {custo_total:,.2f}')
    logger.debug(f'Remaining: R$ {sobra:,.2f}')
    logger.debug(f'Utilization: {utilizacao:.1f}%')

    if resultados:
        df_resultado = pd.DataFrame(resultados)
        df_resultado = df_resultado.sort_values('Valor_Compra', ascending=False)

        logger.info('Displaying Linear Programming results for final user')
        logger.debug(f'Final results:\n{df_resultado}')

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
            print(f'Utilização:         {utilizacao:.1f}%')
            
            if sobra > 0:
                print(f'\nSobra de R$ {sobra:,.2f} vai para SELIC')
                logger.info(f'Remaining R$ {sobra:,.2f} will go to SELIC')
        
        logger.info('Linear Programming results displayed successfully')
        return df_resultado
    else:
        logger.warning('No purchases made - optimization resulted in empty solution')
        return None
    
def otimizar_aporte_lp(df, valor_aporte, valor_carteira=None, k_min=None):
    logger.info('Starting Linear Programming optimization...')
    logger.debug(f'Contribution: R$ {valor_aporte:,.2f}, Portfolio value: {valor_carteira}, Min cardinality: {k_min}')
    
    df = df.copy()

    if valor_carteira is None:
        valor_carteira = df['Total'].sum()
        logger.debug(f'Portfolio value calculated from data: R$ {valor_carteira:,.2f}')

    df['Valor Ideal'] = df['% Ideal - Ref.'] * (valor_carteira + valor_aporte)
    df['deficit'] = (df['Valor Ideal'] - df['Total']).clip(lower=0)
    df['Qtd_nec'] = 0.0
    df['Custo_real'] = 0.0
    
    total_deficit = df['deficit'].sum()
    assets_with_deficit = (df['deficit'] > 0).sum()
    logger.debug(f'Total deficit: R$ {total_deficit:,.2f} across {assets_with_deficit} assets')

    logger.info('Creating LP problem...')
    prob = pl.LpProblem('Otimizacao_Aporte', pl.LpMinimize)
    
    qt_rf, qt_rv, gap, sel = criar_variaveis_lp(df)
    adicionar_restricoes_lp(prob, df, qt_rf, qt_rv, gap, sel, valor_aporte, k_min)
    definir_objetivo_lp(prob, gap)
    
    logger.info('Solving LP problem...')
    prob.solve(pl.PULP_CBC_CMD(msg=0))
    
    status = pl.LpStatus[prob.status]
    logger.info(f'LP solver status: {status}')
    
    if status == "Optimal":
        logger.info('Optimal solution found')
        objective_value = prob.objective.value()
        logger.debug(f'Objective value (total gaps): {objective_value:.2f}')
        
        df_out = extrair_resultados_lp(df, qt_rf, qt_rv, valor_aporte, show=True)
        logger.info('LP optimization completed successfully')
        return df_out
    else:
        logger.error(f'LP optimization failed with status: {status}')
        return None