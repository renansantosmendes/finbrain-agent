---
name: buy-sell-recommendation
description: Gera uma sugestão de compra, manutenção ou venda para uma ação, cruzando preços históricos, simulação de preços futuros sintéticos (GARCH) e detecção de outliers na série histórica. Use para pedidos como "devo comprar ou vender X?", "vale a pena comprar essa ação agora?", "me dá um sinal de compra/venda", "essa ação está cara ou é hora de vender?", "recomendação de compra e venda para o papel".
---
# Skill de Recomendação de Compra/Venda

## Quando Usar
- O usuário pede uma **sugestão/sinal de ação** sobre um ticker: comprar, vender ou manter/aguardar.
- Pedidos como "devo comprar X agora?", "é hora de vender Y?", "me dá uma recomendação para o papel Z", "essa ação está com preço estranho, ainda vale a pena?".
- **Não usar para:**
  - Apenas cotação/histórico sem pedido de decisão → `stock-analysis`.
  - Apenas indicadores de balanço → `fundamental-analysis`.
  - Apenas leitura de gráfico (RSI/MACD/médias) sem pedido de decisão final → `technical-analysis`.
  - Apenas projeção de cenários futuros, sem pedido de "comprar ou vender" → `market-scenario-simulation`.
  - Esta skill é a que **combina** histórico + simulação futura + qualidade dos dados (outliers) para chegar a um veredito acionável.

## Ferramentas Obrigatórias
Esta skill exige, no mínimo, as três consultas abaixo — nunca responda com base apenas em uma delas:

| Ordem | Ferramenta | Papel |
|---|---|---|
| 1 | `collect_yfinance_data(ticker, period, interval)` | Preço atual e série de preços históricos (base de tudo) |
| 2 | `detect_price_outliers(ticker, prices, method="zscore", threshold=2.5)` | Identifica movimentos anômalos/pontos fora da curva na série histórica |
| 3 | `generate_synthetic_stock_series_garch_arch(ticker, start_date, n_days, n_series, initial_price, ...)` | Simula caminhos de preço futuros (GARCH/Monte Carlo) |

Se o agente julgar necessário para reforçar a recomendação (ex.: usuário pede confirmação técnica, ou o sinal ficou no limite entre duas categorias), ele **pode** também chamar `collect_technical_indicators` (RSI/MACD/médias) e/ou `collect_fundamental_indicators` — a critério do agente, não são obrigatórias.

## Protocolo de Execução

1. **Identificação do Ticker:** identifique o ticker mencionado e normalize com `.SA` se for ação brasileira sem sufixo (ex.: "Petrobras" → `PETR4.SA`).

2. **Preços Históricos:** chame `collect_yfinance_data(ticker, period="6mo", interval="1d")` (ou período maior se o usuário pedir "no longo prazo"). Extraia `current_price` e a série de fechamentos históricos.

3. **Detecção de Outliers:** chame `detect_price_outliers` passando a série de fechamentos obtida no passo 2.
   - Considere **outlier recente** qualquer ponto fora da curva nos últimos 5 pregões — isso é motivo de cautela extra (pode indicar evento fora do padrão: notícia, fato relevante, erro de dado).
   - Considere **série ruidosa** quando a proporção de outliers na janela analisada for maior que 10% dos pontos — isso reduz a confiança de qualquer sinal técnico ou estatístico.
   - Se a ferramenta não retornar nenhum outlier, siga normalmente sem penalidade de confiança.

4. **Simulação de Preços Futuros:** chame `generate_synthetic_stock_series_garch_arch` com:
   - `ticker`, `start_date` = data de hoje, `initial_price` = `current_price` do passo 2, `value_type="close"`.
   - `n_days=30` e `n_series=100` como padrão, salvo horizonte/quantidade diferente pedido pelo usuário.
   - `mode="simulate"` com os parâmetros GARCH(1,1) padrão da ferramenta, a menos que o usuário forneça retornos históricos explícitos e peça calibração (nesse caso, `mode="fit_and_simulate"` com `historical_returns`).
   - Para o último dia (`n_days`) de cada série, calcule P10 (pessimista), P50/mediana (base) e P90 (otimista), e a variação percentual de cada um em relação ao `current_price`.

5. **Ferramentas Opcionais (a critério do agente):** se o sinal do passo 6 cair perto de uma fronteira (ex.: variação da mediana entre -2% e +2%), ou o usuário pedir "confirmação técnica"/"fundamentos", chame `collect_technical_indicators` e/ou `collect_fundamental_indicators` para desempatar, citando o resultado extra na avaliação.

6. **Regra de Decisão** (baseada na variação percentual da mediana simulada (P50) vs. `current_price`):

   | Variação da mediana (P50) vs. preço atual | Sinal |
   |---|---|
   | > +5% | 🟢 **COMPRA** |
   | entre -5% e +5% | ⚪ **MANTER** |
   | < -5% | 🔴 **VENDA** |

7. **Ajuste de Confiança** (aplicado sobre o sinal do passo 6, nunca inverte o sinal, apenas qualifica):
   - **Alta confiança:** sem outliers recentes, série não ruidosa, e spread entre P10 e P90 estreito (< 15% do `current_price`).
   - **Confiança reduzida:** outlier recente (últimos 5 pregões) OU série ruidosa (>10% de outliers) OU spread entre P10 e P90 amplo (≥ 15% do `current_price`) — sinalize isso explicitamente na resposta e recomende cautela/reavaliação em vez de convicção total.
   - Se **mais de um** desses fatores de risco ocorrer ao mesmo tempo, rebaixe explicitamente para "sinal de baixa confiança" e reforce que o usuário deve olhar o contexto (notícias, fundamentos) antes de agir.

8. **Alerta de Isenção:** você DEVE deixar claro que (a) a simulação é estatística (GARCH), não uma previsão garantida, e (b) a resposta não constitui recomendação oficial de investimento.

## Formato de Resposta Obrigatório

### 🧭 Recomendação: [Inserir Ticker]
* **Preço Atual:** [preço] em [data]
* **Sinal:** [🟢 COMPRA / ⚪ MANTER / 🔴 VENDA] — confiança [Alta/Reduzida]
* **Cenário simulado ([N] dias, [N] simulações):**

| Cenário | Preço projetado | Variação vs. atual |
|---|---|---|
| 🔴 Pessimista (P10) | [preço] | [%] |
| ⚪ Base (mediana) | [preço] | [%] |
| 🟢 Otimista (P90) | [preço] | [%] |

* **Outliers na série histórica:** [Nenhum detectado / N outliers, sendo o mais recente em DD/MM/AAAA — motivo de cautela]

### 🧠 Avaliação da IA
[Parágrafo conectando: (1) o que os preços históricos mostram, (2) o que a simulação de cenários sugere para o futuro, (3) se a presença/ausência de outliers reforça ou enfraquece a confiança no sinal, e (4) se alguma ferramenta opcional (técnica/fundamentalista) foi usada para desempate, citar o resultado.]

> **Aviso:** Esta recomendação combina dados históricos com uma simulação estatística (GARCH 1,1) sobre caminhos de preço sintéticos e detecção automática de outliers. Não é uma previsão real de mercado, resultados passados e modelos estatísticos não garantem resultados futuros, e **não constitui recomendação oficial de compra ou venda de ativos**. Consulte um profissional antes de investir.
