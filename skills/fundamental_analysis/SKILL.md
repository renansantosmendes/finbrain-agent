---
name: fundamental-analysis
description: Avalia a saúde financeira de uma empresa a partir de indicadores fundamentalistas (P/L, P/VP, ROE, margens, endividamento) para julgar se está cara, barata ou saudável.
---
# Skill de Análise Fundamentalista

## Quando Usar
- Sempre que o usuário perguntar se uma ação está "cara ou barata", pedir para avaliar os "fundamentos", "saúde financeira", "balanço" ou comparar indicadores como P/L, P/VP, ROE, margem ou dívida de uma empresa.
- Não usar para perguntas apenas sobre preço/variação do dia (isso é `stock-analysis`) nem sobre médias móveis/tendência gráfica (isso é `technical-analysis`).

## Protocolo de Execução
1. **Identificação do Ticker:** Identifique o ticker mencionado. Se faltar o sufixo e for ação brasileira, adicione `.SA`.
2. **Coleta de Dados:** Chame `collect_fundamental_indicators` passando o ticker.
3. **Análise de Regras:**
   - **P/L:** Se não houver referência de setor, apenas reporte o valor e explique que P/L baixo pode indicar barganha ou risco, dependendo do setor.
   - **ROE:** Acima de 15% (0.15) é considerado saudável; abaixo de 5% é fraco.
   - **Margem Líquida:** Acima de 10% é sólida; negativa indica empresa operando no prejuízo.
   - **Dívida/Patrimônio:** Acima de 100 (ou 1.0, dependendo da unidade retornada) merece alerta de endividamento elevado.
   - **Dividend Yield:** Se presente e acima de 6%, destaque como possível ação pagadora de dividendos.
4. **Alerta de Isenção:** Você DEVE incluir o aviso legal de que isso não é recomendação oficial de investimento.

## Formato de Resposta Obrigatório
### 🏦 Análise Fundamentalista: [Inserir Ticker]
* **P/L:** [valor]
* **P/VP:** [valor]
* **ROE:** [valor em %]
* **Margem Líquida:** [valor em %]
* **Dívida/Patrimônio:** [valor]
* **Dividend Yield:** [valor em %]

### 🧠 Avaliação da IA
[Parágrafo interpretando os indicadores acima com base nas regras do protocolo]

> **Aviso:** Esta análise é automatizada e baseada em dados públicos. Não constitui recomendação de compra ou venda de ativos.
