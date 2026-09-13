---
name: technical-analysis
description: Realiza análise técnica de gráfico (médias móveis, RSI, MACD) para identificar momentum, sobrecompra/sobrevenda e cruzamentos de tendência.
---
# Skill de Análise Técnica

## Quando Usar
- Sempre que o usuário pedir análise de "gráfico", "tendência técnica", "RSI", "MACD", "média móvel", "momento de compra/venda" ou "sinal técnico" de uma ação.
- Não usar para pedidos de indicadores de balanço/fundamentos (isso é `fundamental-analysis`) nem para cotação simples do dia (isso é `stock-analysis`).

## Protocolo de Execução
1. **Identificação do Ticker:** Identifique o ticker mencionado, adicionando `.SA` se for ação brasileira e o sufixo não tiver sido informado.
2. **Coleta de Dados:** Chame `collect_technical_indicators` passando o ticker (use período padrão de 3 meses, a menos que o usuário peça outro horizonte).
3. **Análise de Regras:**
   - **Médias Móveis:** Se `moving_average_9` > `moving_average_21`, sinalize possível tendência de curto prazo de alta (cruzamento otimista); se for o inverso, tendência de baixa.
   - **RSI:** Acima de 70 indica sobrecompra (risco de correção); abaixo de 30 indica sobrevenda (possível oportunidade); entre 30 e 70 é neutro.
   - **MACD:** Se `macd` > `macd_signal`, momentum comprador; se `macd` < `macd_signal`, momentum vendedor.
4. **Alerta de Isenção:** Você DEVE incluir o aviso legal de que isso não é recomendação oficial de investimento e que indicadores técnicos não garantem resultados futuros.

## Formato de Resposta Obrigatório
### 📊 Análise Técnica: [Inserir Ticker]
* **Preço Atual:** [preço]
* **Médias Móveis (9/21/50):** [valores]
* **RSI (14):** [valor] — [Sobrecomprado / Sobrevendido / Neutro]
* **MACD:** [valor] vs Sinal [valor] — [Momentum Comprador / Vendedor]

### 🧠 Avaliação da IA
[Parágrafo interpretando os sinais técnicos combinados, com base nas regras do protocolo]

> **Aviso:** Esta análise é automatizada, baseada em dados públicos e indicadores técnicos passados, que não garantem resultados futuros. Não constitui recomendação de compra ou venda de ativos.
