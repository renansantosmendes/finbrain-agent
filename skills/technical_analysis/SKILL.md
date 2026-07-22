---
name: cripto
description: Use esta skill para preços, candles ou order book de criptomoedas (Bitcoin, Ethereum, etc.) em exchanges. Ative para pedidos como "qual o preço do Bitcoin", "mostre o histórico do Ethereum", ou "qual o spread do BTC na Binance".
---

# Criptomoedas (ccxt)

Esta skill usa a biblioteca `ccxt` para buscar dados reais de criptomoedas em exchanges.

## Quando usar
- Qualquer pergunta sobre preço, histórico ou order book de criptomoedas.

## Ferramentas disponíveis
- `get_crypto_price(exchange_name, symbol)`: preço atual, máxima/mínima 24h, variação.
- `get_crypto_ohlcv(exchange_name, symbol, timeframe, limit)`: histórico de candles.
- `get_crypto_order_book(exchange_name, symbol, limit)`: melhores preços de compra/venda e spread.
- `list_available_exchanges()`: lista de exchanges suportadas, caso o usuário pergunte quais existem.

## Como usar
1. Se o usuário não especificar a exchange, use "binance" como padrão -- NÃO pergunte, apenas prossiga.
2. Símbolos seguem o formato "BASE/QUOTE" em maiúsculas (ex: BTC/USDT, ETH/USD).
3. Se o usuário usar o nome comum da moeda (ex: "Bitcoin"), converta para o símbolo (BTC/USDT).

