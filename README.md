# FinBrain Agent

Agente financeiro construído com [DeepAgents](https://github.com/langchain-ai/deepagents) que responde perguntas sobre ações, criptomoedas e indicadores macroeconômicos, consumindo ferramentas de um servidor **MCP** remoto e orientando seu comportamento por meio de **Skills** em Markdown.

---

## Visão Geral

O FinBrain separa duas responsabilidades que normalmente se misturam em agentes de LLM:

| Camada | Onde vive | Papel |
|---|---|---|
| **Ferramentas (tools)** | Servidor MCP remoto (`https://finbrain-mcp.vercel.app/mcp`) | *O que* o agente consegue fazer: buscar cotações, candles, séries do BCB, indicadores do Banco Mundial |
| **Skills** | [skills/](skills/) — arquivos `SKILL.md` | *Como* o agente deve agir: quando acionar cada ferramenta, quais regras de interpretação aplicar e em que formato responder |

Assim, adicionar comportamento novo geralmente não exige mexer em código Python — basta escrever ou ajustar um `SKILL.md`.

### Fluxo de execução

```
Pergunta do usuário
        │
        ▼
 create_deep_agent  ──── system prompt versionado no Langfuse
        │                (FINBRAIN_SYSTEM_PROMPT)
        ├── skills/ ──── seleciona a skill pela descrição do front-matter
        │
        ├── MCP client ── carrega as tools do servidor remoto
        │
        └── FilesystemBackend ── lê/escreve arquivos locais (ex.: gráficos gerados)
        │
        ▼
 Resposta formatada conforme o protocolo da skill
```

### Observabilidade

Todas as execuções são rastreadas no **Langfuse** (`CallbackHandler`), com `session_id` e tags (`skills-demo`, `financial-agent`). O prompt de sistema também é buscado do Langfuse (`langfuse.get_prompt("FINBRAIN_SYSTEM_PROMPT")`), o que permite versionar e alterar o comportamento base do agente sem novo deploy.

---

## Estrutura do repositório

```
finbrain-agent/
├── main_mcp.py          # ponto de entrada: monta o agente e consome as tools do MCP
├── requirements.txt     # dependências
├── .env                 # chaves de API (não versionado)
└── skills/              # skills em Markdown que orientam o agente
    ├── stock_analysis/
    ├── fundamental_analysis/
    ├── technical_analysis/
    ├── asset_comparison/
    ├── market_scenario_simulation/
    ├── cripto/
    ├── macro_brasil/
    └── macro_global/
```

---

## Skills disponíveis

Sete skills cobrem quatro domínios. A documentação detalhada de cada uma está em [skills/README.md](skills/README.md).

### 📈 Ações

| Skill | Quando é acionada | Ferramenta principal |
|---|---|---|
| [`stock-analysis`](skills/stock_analysis/SKILL.md) | Preço atual, histórico ou desempenho de um ticker | `collect_yfinance_data` |
| [`fundamental-analysis`](skills/fundamental_analysis/SKILL.md) | "Está cara ou barata?", balanço, P/L, ROE, endividamento | `collect_fundamental_indicators` |
| [`technical-analysis`](skills/technical_analysis/SKILL.md) | Gráfico, tendência, RSI, MACD, médias móveis | `collect_technical_indicators` |
| [`asset-comparison`](skills/asset_comparison/SKILL.md) | Dois ou mais tickers na mesma pergunta | `compare_assets` |
| [`market-scenario-simulation`](skills/market_scenario_simulation/SKILL.md) | Projeção de cenários futuros, simulação de mercado, Monte Carlo | `generate_synthetic_stock_series_garch_arch` |

### ₿ Criptomoedas

| Skill | Quando é acionada | Ferramentas |
|---|---|---|
| [`cripto`](skills/cripto/SKILL.md) | Preço, candles ou order book de cripto | `get_crypto_price`, `get_crypto_ohlcv`, `get_crypto_order_book`, `list_available_exchanges` |

Exchange padrão: `binance`. Símbolos no formato `BASE/QUOTE` (ex.: `BTC/USDT`).

### 🇧🇷 Macroeconomia Brasil

| Skill | Quando é acionada | Ferramentas |
|---|---|---|
| [`macro-brasil`](skills/macro_brasil/SKILL.md) | Selic, IPCA, CDI, PIB, dólar PTAX, Boletim Focus | `get_bcb_series`, `get_ptax_dolar_periodo`, `get_market_expectations` |

Fonte: Banco Central do Brasil via `python-bcb`.

### 🌎 Macroeconomia Global

| Skill | Quando é acionada | Ferramentas |
|---|---|---|
| [`macro-global`](skills/macro_global/SKILL.md) | PIB, inflação, desemprego, população de qualquer país | `get_world_bank_indicator`, `search_world_bank_indicator`, `compare_countries_latest` |

Fonte: Banco Mundial via `wbdata`. Códigos de país em ISO de 3 letras (`BRA`, `USA`, `CHN`).

---

## Instalação

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

pip install -r requirements.txt
```

### Variáveis de ambiente

Crie um `.env` na raiz:

```env
OPENAI_API_KEY=...

LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=https://cloud.langfuse.com
```

O Langfuse é obrigatório na configuração atual, porque o prompt de sistema é carregado de lá.

---

## Uso

Contra o servidor MCP publicado:

```bash
python main_mcp.py
```

Contra um servidor MCP local ou alternativo:

```bash
python main_mcp.py http://localhost:8000/mcp
```

A pergunta enviada ao agente está fixa em [main_mcp.py:48](main_mcp.py#L48) — altere-a para testar outros cenários:

```python
inputs = {"messages": [{"role": "user", "content": "Qual a Selic atual?"}]}
```

---

## Como escrever uma nova skill

Crie `skills/<nome>/SKILL.md` seguindo o padrão dos arquivos existentes:

```markdown
---
name: nome-da-skill
description: Descrição usada pelo agente para decidir quando acionar esta skill.
---
# Título

## Quando Usar
- Gatilhos claros, e quais skills usar em vez desta em casos limítrofes.

## Protocolo de Execução
1. Normalização de entrada (ex.: adicionar `.SA` para ações brasileiras)
2. Qual ferramenta chamar e com quais parâmetros padrão
3. Regras de interpretação dos dados retornados
4. Avisos obrigatórios

## Formato de Resposta Obrigatório
[Template exato da resposta]
```

Boas práticas observadas nas skills atuais:

- **`description` é o roteador.** É por ela que o agente escolhe a skill — descreva os gatilhos, não a implementação.
- **Delimite fronteiras.** Diga explicitamente qual skill usar quando o pedido não é para esta (`technical-analysis` aponta para `fundamental-analysis` e `stock-analysis`).
- **Regras numéricas explícitas.** RSI > 70 = sobrecompra, ROE > 15% = saudável. Isso evita interpretações inventadas pelo modelo.
- **Nunca inventar dados.** Toda skill exige chamada de ferramenta antes de responder.
- **Defaults em vez de perguntas.** Sem exchange informada, use `binance`; sem período, use 30 dias ou 3 meses.
- **Aviso legal.** Skills de ações exigem o disclaimer de que a análise não é recomendação de investimento.

---

## Stack

| Componente | Uso |
|---|---|
| `deepagents` | Orquestração do agente e carregamento das skills |
| `langchain-mcp-adapters` | Cliente MCP (`streamable_http`) |
| `langfuse` | Observabilidade e versionamento do system prompt |
| `yfinance` | Dados de ações |
| `ccxt` | Dados de criptomoedas |
| `python-bcb` | Séries do Banco Central do Brasil |
| `wbdata` | Indicadores do Banco Mundial |
| `arch` | Modelagem GARCH para simulação de cenários (lado do MCP) |
| `fastapi` / `uvicorn` | Servidor MCP |

---

## Aviso

Este projeto é experimental e educacional. As análises são automatizadas, baseadas em dados públicos, e **não constituem recomendação de compra ou venda de ativos**.
