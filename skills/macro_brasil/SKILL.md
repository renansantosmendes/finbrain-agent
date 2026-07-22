---
name: macro-brasil
description: Use esta skill para indicadores macroeconômicos e cambiais especificamente do Brasil, como Selic, IPCA, CDI, dólar PTAX ou expectativas do Boletim Focus. Ative para pedidos como "qual a Selic atual", "cotação do dólar", ou "qual a expectativa de IPCA para 2026".
---

# Macroeconomia do Brasil (Banco Central)

Esta skill usa a biblioteca `bcb` (python-bcb) para dados oficiais do Banco Central do Brasil.

## Quando usar
- Perguntas especificamente sobre indicadores econômicos do Brasil.
- Perguntas sobre cotação do dólar (PTAX) ou expectativas de mercado (Boletim Focus).

## Ferramentas disponíveis
- `get_bcb_series(nome_serie, start_date, end_date)`: séries históricas. Nomes válidos: selic, ipca, cdi, pib, dolar_comercial.
- `get_ptax_dolar_periodo(start_date, end_date)`: cotação diária de fechamento do dólar (formato de data "MM-DD-YYYY").
- `get_market_expectations(indicador, data_referencia)`: expectativas do Boletim Focus para um indicador e ano.

## Como usar
1. Se o usuário não especificar um período, use os últimos 30 dias como padrão.
2. Nunca invente valores -- sempre busque via ferramenta antes de responder.