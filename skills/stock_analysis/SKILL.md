---
name: stock-analysis
description: Coleta dados históricos e atuais de ações (tickers) e realiza uma análise técnica/fundamentalista básica.
---
# Skill de Análise de Ações (yfinance)

## Quando Usar
- Sempre que o usuário perguntar sobre o preço atual, histórico, desempenho ou pedir uma recomendação/análise de uma ação específica (ex: PETR4, VALE3, AAPL, TSLA).

## Protocolo de Execução
1. **Identificação do Ticker:** Identifique o ticker da ação mencionada. Se o usuário esquecer o código (ex: "Petrobras"), tente deduzir ou adicione o sufixo `.SA` se for uma ação brasileira (ex: `PETR4.SA`).
2. **Coleta de Dados:** Chame a ferramenta `collect_yfinance_data` passando o ticker identificado. Use `period` e `interval` padrão (`1mo`/`1d`), a menos que o usuário peça um horizonte ou granularidade diferente (ex: "últimos 5 dias", "intraday de hoje").
3. **Análise de Regras:** Com o JSON retornado pela ferramenta, você deve avaliar:
   - **Preço vs Média:** Se o `current_price` estiver abaixo da `fifty_day_average`, mencione que a ação está em tendência de baixa no curto prazo. Se estiver acima, tendência de alta.
   - **Volume:** Se o volume atual for muito maior que a média, destaque que há alto interesse institucional no papel hoje.
4. **Alerta de Isenção:** Você DEVE incluir o aviso legal de que isso não é uma recomendação oficial de investimento.

## Formato de Resposta Obrigatório
### 📈 Análise de Ativo: [Inserir Ticker]
* **Preço Atual:** R$ [Preço] (ou US$ se internacional)
* **Variação no Dia:** [X]%
* **Tendência (Média 50d):** [Acima da Média / Abaixo da Média]

### 🧠 Avaliação da IA
[Insira aqui o seu parágrafo de análise com base nas regras acima]

> **Aviso:** Esta análise é automatizada e baseada em dados públicos. Não constitui recomendação de compra ou venda de ativos.
