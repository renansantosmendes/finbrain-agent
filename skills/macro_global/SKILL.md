---
name: macro-global
description: Use esta skill para dados macroeconômicos internacionais do Banco Mundial, como PIB, inflação, desemprego ou população de países. Ative para pedidos como "qual o PIB da China", "compare a inflação de Brasil e EUA", ou "como está o desemprego na Argentina".
---

# Macroeconomia Global (Banco Mundial)

Esta skill usa dados do Banco Mundial via `wbdata` para indicadores macroeconômicos internacionais.

## Quando usar
- Perguntas sobre indicadores econômicos de QUALQUER país (exceto perguntas específicas sobre o Brasil, que usam a skill macro-brasil).
- Comparações entre países.

## Ferramentas disponíveis
- `get_world_bank_indicator(indicator_code, countries, start_year, end_year)`: busca um indicador para uma lista de países.
- `search_world_bank_indicator(query)`: busca o código de um indicador por palavra-chave, quando você não souber o código exato.
- `compare_countries_latest(indicator_code, countries)`: compara o valor mais recente entre países, ordenado do maior para o menor.

## Como usar
1. Se não souber o código do indicador, use `search_world_bank_indicator` primeiro.
2. Códigos de país são ISO de 3 letras (ex: BRA, USA, CHN, ARG).
3. Sempre inclua o ano/período dos dados na resposta final.