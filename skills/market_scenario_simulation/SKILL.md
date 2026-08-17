---
name: market-scenario-simulation
description: Use esta skill para projeção de cenários futuros e simulação de mercado de uma ação, com base em modelagem estatística (GARCH). Ative para pedidos como "simule cenários para a ação X", "qual o range de preços possível em 30 dias", "projeção de preço", "simulação de Monte Carlo", "cenário otimista e pessimista para o papel", ou "qual a volatilidade esperada".
---
# Skill de Simulação de Cenários (GARCH/Monte Carlo)

## Quando Usar
- O usuário pede uma **projeção futura** de preço (não histórico), um **range de cenários** (otimista/base/pessimista), ou uma **simulação de mercado/Monte Carlo** para uma ação.
- **Não usar para:** preço atual/histórico (`stock-analysis`), indicadores de balanço (`fundamental-analysis`) ou tendência de gráfico com dados passados (`technical-analysis`). Esta skill é a única que projeta preços **futuros ainda não observados**.

## Protocolo de Execução
1. **Identificação do Ticker:** Identifique o ticker e normalize com sufixo `.SA` se for ação brasileira (ex: "Petrobras" → `PETR4.SA`).
2. **Preço Base:** Chame `collect_yfinance_data(ticker, period="5d", interval="1d")` para obter o `current_price`, que será usado como `initial_price` da simulação.
3. **Horizonte e Repetições:**
   - Se o usuário não especificar um horizonte, use `n_days=30` (dias úteis).
   - Use `n_series=100` simulações, salvo pedido explícito de outro valor.
   - `start_date` deve ser a data de hoje.
4. **Execução da Simulação:** Chame `generate_synthetic_stock_series_garch_arch` com:
   - `ticker`, `start_date`, `n_days`, `n_series`, `initial_price` (do passo 2), `value_type="close"`.
   - `mode="simulate"` com os parâmetros GARCH(1,1) padrão da própria ferramenta (`mu=0.0`, `omega=0.02`, `alpha=0.1`, `beta=0.85`), a menos que o usuário forneça retornos históricos explícitos e peça calibração (nesse caso use `mode="fit_and_simulate"` passando `historical_returns`).
5. **Agregação dos Resultados:** A ferramenta retorna um JSON `{"serie_1": [...], ..., "serie_n": [...]}` com um caminho de preços por simulação. Para o **último dia** (`n_days`) de cada série:
   - Ordene os valores finais de todas as séries.
   - **Cenário Pessimista** = percentil 10 (P10).
   - **Cenário Base** = mediana (P50).
   - **Cenário Otimista** = percentil 90 (P90).
   - Calcule a variação percentual de cada cenário em relação ao `initial_price`.
6. **Alerta de Isenção:** Você DEVE deixar claro que os valores são **simulações estatísticas (GARCH)**, não previsões garantidas, e incluir o aviso legal de que não é recomendação de investimento.

## Formato de Resposta Obrigatório
### 🎲 Simulação de Cenários: [Inserir Ticker]
* **Preço Atual (base):** [Preço] em [data]
* **Horizonte simulado:** [N] dias úteis
* **Nº de simulações:** [N]

| Cenário | Preço projetado | Variação vs. atual |
|---|---|---|
| 🔴 Pessimista (P10) | [preço] | [%] |
| ⚪ Base (mediana) | [preço] | [%] |
| 🟢 Otimista (P90) | [preço] | [%] |

### 🧠 Avaliação da IA
[Parágrafo interpretando a amplitude entre os cenários — quanto maior o espalhamento entre P10 e P90, maior a volatilidade projetada]

> **Aviso:** Esta simulação é gerada por um modelo estatístico (GARCH 1,1) sobre caminhos de preço sintéticos e **não é uma previsão real de mercado**. Resultados passados e modelos estatísticos não garantem resultados futuros. Não constitui recomendação de compra ou venda de ativos.
