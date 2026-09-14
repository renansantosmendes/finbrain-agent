---
name: credit-card-insights
description: Analisa faturas de cartão de crédito em PDF enviadas pelo usuário para dar insights sobre os gastos, identificar assinaturas e recorrências, alertar sobre juros/rotativo e sugerir cortes de custo para evitar dívidas. Use para "analise minha fatura", "onde estou gastando mais", "como reduzir meus gastos no cartão", "estou pagando juros/rotativo", "compare minhas últimas faturas".
---
# Skill de Análise de Faturas de Cartão de Crédito

## Quando Usar
- O usuário enviou uma ou mais faturas de cartão de crédito em PDF (via `/invoices/analyze` na API ou como documento no Telegram) e quer entender/reduzir seus gastos.
- Perguntas como "analise minha fatura", "onde posso cortar gastos?", "estou entrando no rotativo?", "compare essas faturas dos últimos meses".
- Perguntas sobre **como enviar** uma fatura para análise (ex.: "como te mando minha fatura?") — mesmo sem arquivo anexado.
- **Não usar para:** análise de ações/ativos financeiros (isso é `stock-analysis` e as demais skills de ações) — esta skill é só sobre gastos pessoais em fatura de cartão.

## Ferramenta Obrigatória
Sempre que esta skill for acionada, chame `read_credit_card_invoices()` (sem argumentos) — ela retorna o texto extraído da(s) fatura(s) enviada(s) **nesta mensagem**.

- Essa ferramenta **não busca nada de fora**: ela só lê o que já foi enviado no mesmo turno. Nunca invente dados de fatura — se a ferramenta não retornar nenhuma fatura, não prossiga com números.

## Protocolo de Execução

1. **Chame `read_credit_card_invoices()`.**

2. **Se `invoices` vier vazio** (nenhuma fatura enviada): explique ao usuário como enviar, sem inventar análise nenhuma:
   - Pela API: `POST /invoices/analyze` (multipart), campo `files` com até **20 arquivos PDF**, campo opcional `message` com a pergunta.
   - Pelo Telegram: anexar o PDF da fatura como documento no chat (um arquivo por mensagem — para analisar várias de uma vez, use a API).
   - Avise que PDFs **protegidos por senha não são suportados** nesta versão — o usuário deve remover a senha antes de enviar.

3. **Para cada item em `invoices`:**
   - Se `ok=false`: reporte o `filename` e o `error` (ex.: "protegido por senha", "PDF escaneado sem texto") — não tente adivinhar o conteúdo desse arquivo.
   - Se `ok=true`: use o campo `text` (texto bruto extraído do PDF) para identificar transações (data, descrição, valor), taxas, juros e o total da fatura. O formato varia por banco — interprete o texto como uma tabela de transações mesmo que venha sem colunas alinhadas.
   - Se `truncated=true`, mencione que a análise considerou apenas o início do texto extraído (fatura muito longa).

4. **Análise (quando há pelo menos uma fatura `ok=true`):**
   - **Categorize** os gastos identificados por tipo (alimentação, transporte, assinaturas/streaming, compras, saúde, etc.) com base na descrição de cada transação.
   - **Identifique recorrências/assinaturas** (mesma descrição/valor aparecendo em múltiplos meses, se houver mais de uma fatura, ou valores típicos de assinatura como streaming/apps).
   - **Alerta de dívida:** se o texto mencionar "juros", "multa", "rotativo", "IOF" ou encargos financeiros, destaque isso como prioridade máxima na resposta — é o sinal mais forte de risco de dívida.
   - **Se houver mais de uma fatura**, compare a evolução do total e das categorias entre elas (aumento/queda mês a mês).
   - **Sugestões de corte:** aponte as 2-3 categorias/itens com maior potencial de economia, com base no que apareceu nos dados — nunca sugira cortar algo que não apareceu na fatura.

5. **Alerta de Isenção:** você DEVE deixar claro que (a) a leitura do PDF pode conter erros de extração, (b) isso não é aconselhamento financeiro profissional, e (c) dados sensíveis devem ser tratados com cuidado pelo próprio usuário.

## Formato de Resposta Obrigatório

### 💳 Análise de Fatura(s)
* **Arquivo(s) analisado(s):** [lista de filenames com `ok=true`] — [lista de filenames com erro, se houver, e o motivo]
* **Total identificado:** [soma aproximada, por fatura se houver mais de uma]

| Categoria | Valor aproximado | % do total |
|---|---|---|
| [categoria 1] | ... | ... |
| [categoria 2] | ... | ... |

* **Assinaturas/recorrências identificadas:** [lista, ou "nenhuma identificada"]
* **⚠️ Juros/rotativo/multas:** [se houver, destaque em negrito o valor e o risco; se não houver, diga que não foi identificado]

### 🧠 Avaliação da IA
[Parágrafo com as 2-3 sugestões de corte de custo mais relevantes, e — se houver mais de uma fatura — a tendência de gastos entre elas]

> **Aviso:** Esta análise é automatizada, baseada no texto extraído do(s) PDF(s) enviado(s) (sujeito a erros de leitura) e não constitui aconselhamento financeiro profissional. Faturas protegidas por senha não são suportadas nesta versão.
