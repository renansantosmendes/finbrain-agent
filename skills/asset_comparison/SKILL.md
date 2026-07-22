---
name: asset-comparison
description: Compara dois ou mais ativos lado a lado em preço, variação, P/L e dividend yield, apontando qual parece mais atrativo no critério pedido.
---
# Skill de Comparação de Ativos

## Quando Usar
- Sempre que o usuário mencionar duas ou mais ações na mesma pergunta e pedir para "comparar", "qual é melhor", "qual vale mais a pena" ou "qual pagou mais dividendo".
- Se apenas um ticker for mencionado, use `stock-analysis`, `fundamental-analysis` ou `technical-analysis` em vez desta skill.

## Protocolo de Execução
1. **Identificação dos Tickers:** Liste todos os tickers mencionados, adicionando `.SA` para ações brasileiras sem sufixo.
2. **Coleta de Dados:** Chame `compare_assets` passando todos os tickers separados por vírgula em uma única chamada.
3. **Análise de Regras:**
   - Ordene os ativos pelo critério mais relevante à pergunta do usuário (menor P/L = mais "barato" relativamente; maior dividend yield = melhor pagador de dividendos; maior variação do dia = melhor desempenho no dia).
   - Se o usuário não especificar critério, apresente os três critérios (preço/variação, P/L, dividend yield) e destaque o destaque em cada um.
4. **Alerta de Isenção:** Você DEVE incluir o aviso legal de que isso não é recomendação oficial de investimento.

## Formato de Resposta Obrigatório
### ⚖️ Comparação de Ativos: [Tickers]

| Ticker | Preço | Variação Dia | P/L | Dividend Yield |
|---|---|---|---|---|
| [ticker 1] | ... | ... | ... | ... |
| [ticker 2] | ... | ... | ... | ... |

### 🧠 Avaliação da IA
[Parágrafo indicando qual ativo se destaca em cada critério relevante, sem afirmar qual é "o melhor" de forma categórica]

> **Aviso:** Esta análise é automatizada e baseada em dados públicos. Não constitui recomendação de compra ou venda de ativos.
