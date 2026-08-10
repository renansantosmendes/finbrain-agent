# Skills do FinBrain

Cada subdiretório contém um `SKILL.md`: um documento em Markdown com front-matter YAML que o agente carrega para decidir **quando** agir, **quais ferramentas** chamar e **como formatar** a resposta.

```
skills/<nome>/SKILL.md
```

O front-matter tem dois campos:

- `name` — identificador da skill (kebab-case)
- `description` — texto que o agente usa para rotear a pergunta do usuário até a skill certa

Voltar para o [README principal](../README.md).

---

## Índice

| Skill | Domínio | Diretório |
|---|---|---|
| [`stock-analysis`](#-stock-analysis) | Ações | [stock_analysis/](stock_analysis/SKILL.md) |
| [`fundamental-analysis`](#-fundamental-analysis) | Ações | [fundamental_analysis/](fundamental_analysis/SKILL.md) |
| [`technical-analysis`](#-technical-analysis) | Ações | [technical_analysis/](technical_analysis/SKILL.md) |
| [`asset-comparison`](#️-asset-comparison) | Ações | [asset_comparison/](asset_comparison/SKILL.md) |
| [`cripto`](#-cripto) | Criptomoedas | [cripto/](cripto/SKILL.md) |
| [`macro-brasil`](#-macro-brasil) | Macro Brasil | [macro_brasil/](macro_brasil/SKILL.md) |
| [`macro-global`](#-macro-global) | Macro Global | [macro_global/](macro_global/SKILL.md) |

---

## 📈 `stock-analysis`

**Coleta dados históricos e atuais de ações e faz uma análise básica.**

- **Gatilhos:** preço atual, histórico, desempenho ou pedido genérico de análise de um ticker (PETR4, VALE3, AAPL, TSLA).
- **Ferramenta:** `collect_yfinance_data(ticker, period, interval)` — padrões `1mo` / `1d`.
- **Normalização:** ações brasileiras sem sufixo recebem `.SA` (ex.: `PETR4.SA`).
- **Regras de interpretação:**
  - `current_price` abaixo da `fifty_day_average` → tendência de baixa no curto prazo; acima → tendência de alta.
  - Volume muito acima da média → destacar alto interesse institucional no dia.
- **Saída:** bloco `📈 Análise de Ativo` com preço, variação do dia e tendência de 50 dias, seguido de `🧠 Avaliação da IA` e do disclaimer.

---

## 🏦 `fundamental-analysis`

**Avalia a saúde financeira da empresa por indicadores de balanço.**

- **Gatilhos:** "está cara ou barata", fundamentos, saúde financeira, balanço, P/L, P/VP, ROE, margem, dívida.
- **Não usar para:** preço/variação do dia (`stock-analysis`) nem tendência gráfica (`technical-analysis`).
- **Ferramenta:** `collect_fundamental_indicators(ticker)`
- **Regras de interpretação:**

  | Indicador | Regra |
  |---|---|
  | P/L | Sem referência de setor, apenas reportar — P/L baixo pode ser barganha *ou* risco |
  | ROE | > 15% saudável · < 5% fraco |
  | Margem líquida | > 10% sólida · negativa = prejuízo |
  | Dívida/Patrimônio | > 100 (ou 1.0) → alerta de endividamento elevado |
  | Dividend Yield | > 6% → destacar como pagadora de dividendos |

- **Saída:** bloco `🏦 Análise Fundamentalista` com os seis indicadores, `🧠 Avaliação da IA` e disclaimer.

---

## 📊 `technical-analysis`

**Análise de gráfico: médias móveis, RSI e MACD.**

- **Gatilhos:** gráfico, tendência técnica, RSI, MACD, média móvel, momento de compra/venda, sinal técnico.
- **Não usar para:** indicadores de balanço (`fundamental-analysis`) nem cotação simples (`stock-analysis`).
- **Ferramenta:** `collect_technical_indicators(ticker)` — período padrão de 3 meses.
- **Regras de interpretação:**

  | Indicador | Regra |
  |---|---|
  | Médias móveis | `MM9 > MM21` → cruzamento otimista (alta de curto prazo); inverso → baixa |
  | RSI (14) | > 70 sobrecompra · < 30 sobrevenda · 30–70 neutro |
  | MACD | `macd > macd_signal` → momentum comprador; inverso → vendedor |

- **Saída:** bloco `📊 Análise Técnica` com preço, médias 9/21/50, RSI e MACD, `🧠 Avaliação da IA` e disclaimer reforçando que indicadores passados não garantem resultados futuros.

---

## ⚖️ `asset-comparison`

**Compara dois ou mais ativos lado a lado.**

- **Gatilhos:** dois ou mais tickers na mesma pergunta com "comparar", "qual é melhor", "qual vale mais a pena", "qual pagou mais dividendo".
- **Com um único ticker:** usar `stock-analysis`, `fundamental-analysis` ou `technical-analysis`.
- **Ferramenta:** `compare_assets(tickers)` — todos os tickers separados por vírgula em **uma única chamada**.
- **Regras de interpretação:**
  - Ordenar pelo critério pedido: menor P/L = relativamente mais barato; maior DY = melhor pagador; maior variação do dia = melhor desempenho.
  - Sem critério explícito, apresentar os três (preço/variação, P/L, dividend yield) e destacar o líder em cada um.
  - Nunca afirmar categoricamente qual é "o melhor".
- **Saída:** tabela comparativa `⚖️ Comparação de Ativos`, `🧠 Avaliação da IA` e disclaimer.

---

## ₿ `cripto`

**Preços, candles e order book de criptomoedas via `ccxt`.**

- **Gatilhos:** preço, histórico ou order book de qualquer criptomoeda.
- **Ferramentas:**

  | Ferramenta | Retorna |
  |---|---|
  | `get_crypto_price(exchange_name, symbol)` | Preço atual, máxima/mínima 24h, variação |
  | `get_crypto_ohlcv(exchange_name, symbol, timeframe, limit)` | Histórico de candles |
  | `get_crypto_order_book(exchange_name, symbol, limit)` | Melhores bid/ask e spread |
  | `list_available_exchanges()` | Exchanges suportadas |

- **Convenções:**
  - Exchange padrão `binance` — não perguntar, apenas prosseguir.
  - Símbolos em `BASE/QUOTE` maiúsculo (`BTC/USDT`, `ETH/USD`).
  - Nome comum → símbolo ("Bitcoin" → `BTC/USDT`).

---

## 🇧🇷 `macro-brasil`

**Indicadores macroeconômicos e cambiais do Brasil, via `python-bcb` (Banco Central).**

- **Gatilhos:** Selic, IPCA, CDI, PIB, dólar PTAX, expectativas do Boletim Focus.
- **Ferramentas:**

  | Ferramenta | Observações |
  |---|---|
  | `get_bcb_series(nome_serie, start_date, end_date)` | Séries válidas: `selic`, `ipca`, `cdi`, `pib`, `dolar_comercial` |
  | `get_ptax_dolar_periodo(start_date, end_date)` | Datas no formato `MM-DD-YYYY` |
  | `get_market_expectations(indicador, data_referencia)` | Expectativas do Focus por indicador e ano |

- **Convenções:** sem período informado, usar os últimos 30 dias. Nunca inventar valores — sempre consultar a ferramenta antes de responder.

---

## 🌎 `macro-global`

**Indicadores macroeconômicos internacionais do Banco Mundial, via `wbdata`.**

- **Gatilhos:** PIB, inflação, desemprego, população de qualquer país; comparações entre países.
- **Fronteira:** perguntas específicas sobre o Brasil vão para `macro-brasil`.
- **Ferramentas:**

  | Ferramenta | Uso |
  |---|---|
  | `search_world_bank_indicator(query)` | Descobrir o código do indicador por palavra-chave |
  | `get_world_bank_indicator(indicator_code, countries, start_year, end_year)` | Série do indicador para uma lista de países |
  | `compare_countries_latest(indicator_code, countries)` | Valor mais recente entre países, do maior para o menor |

- **Convenções:** códigos de país em ISO de 3 letras (`BRA`, `USA`, `CHN`, `ARG`); sempre informar o ano/período dos dados na resposta.

---

## Padrões comuns entre as skills

1. **Roteamento pela `description`.** É o único texto que o agente vê antes de escolher — descreva gatilhos, não implementação.
2. **Fronteiras explícitas.** Cada skill de ações declara qual outra usar quando o pedido não é seu.
3. **Regras numéricas em vez de julgamento livre.** Limiares fixos (RSI 70/30, ROE 15%) impedem interpretações inventadas.
4. **Dados sempre por ferramenta.** Nenhuma skill autoriza responder de memória.
5. **Defaults em vez de perguntas de volta.** Exchange, período e intervalo têm padrão definido.
6. **Formato de saída obrigatório.** Cabeçalho com emoji, campos, avaliação da IA e — nas skills de ações — o aviso legal.
