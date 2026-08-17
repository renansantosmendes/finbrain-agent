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

Todas as execuções são rastreadas no **Langfuse** (`CallbackHandler`), com `session_id` e tags (`skills-demo`, `financial-agent`). O prompt de sistema também é buscado do Langfuse (`langfuse.get_prompt("FINBRAIN_SYSTEM_PROMPT")`), o que permite versionar e alterar o comportamento base do agente sem novo deploy. Todos os módulos (`app.py`, `main_mcp.py`, `persistence.py`) também emitem logs estruturados via `logging_config.py` (logger `finbrain`).

### Persistência de conversas

O agente usa memória real entre turnos, e essa memória sobrevive a reinícios de processo (incluindo cold starts em serverless) porque nada fica só em RAM — tudo vive no Postgres (Neon), no schema dedicado `agent_conversations`:

- **Checkpoints do LangGraph** (`AsyncPostgresSaver`) — estado completo da conversa por `thread_id`/`session_id`, o que dá ao agente memória de fato.
- **Tabela `messages`** — log legível (thread_id, role, content, created_at) para consulta/auditoria fora do formato interno do LangGraph.

Ver [persistence.py](persistence.py) para os detalhes de conexão (schema via `search_path`, contorno do pooler do Neon).

---

## Estrutura do repositório

```
finbrain-agent/
├── main_mcp.py           # script de demonstração: roda o agente uma vez via CLI
├── app.py                # API FastAPI (rota /chat) para conversar com o agente por HTTP
├── api/
│   └── index.py          # entrypoint do Vercel: apenas reexporta `app` de app.py
├── vercel.json            # rewrites + config da função serverless
├── persistence.py        # schema Postgres (Neon), checkpointer do LangGraph, log de mensagens
├── logging_config.py     # configuração do logger "finbrain"
├── requirements.txt      # dependências de produção (também usado pelo Vercel)
├── requirements-dev.txt  # + pytest, para CI/dev local
├── pytest.ini             # config dos testes
├── tests/                 # testes unitários (persistence.py e app.py)
├── .github/workflows/ci.yml  # roda pytest em PRs e pushes na main
├── .env                   # chaves de API (não versionado)
└── skills/                # skills em Markdown que orientam o agente
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

## Deploy no Vercel

A API (`app.py`) está pronta para deploy como função serverless Python no Vercel:

- **[api/index.py](api/index.py)** — entrypoint que o Vercel detecta automaticamente (`from app import app`). Não duplica lógica, só reexporta.
- **[vercel.json](vercel.json)** — reescreve todas as rotas para `api/index.py` (necessário para que `/health` e `/chat` funcionem via FastAPI) e define `maxDuration: 60` para a função, já que uma resposta do agente (LLM + tools do MCP + Postgres) pode levar bem mais que os 10s padrão do plano Hobby. **Ajuste esse valor conforme o limite do seu plano Vercel** — Hobby sem Fluid Compute trava em 10s.
- **[.python-version](.python-version)** — fixa `3.12` (verifique nas [runtimes suportadas pelo Vercel](https://vercel.com/docs/functions/runtimes/python) se a versão ainda é válida no momento do deploy).
- **[.vercelignore](.vercelignore)** — exclui `.venv/`, `tests/`, `.github/`, `.env` etc. do pacote enviado.

### Passos

1. Importe o repositório no [dashboard do Vercel](https://vercel.com/new) (framework preset: "Other").
2. Configure as variáveis de ambiente do projeto (Settings → Environment Variables) com os mesmos valores do `.env` local: `OPENAI_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `NEON_DATABASE_URL`.
3. Deploy. A cada push, o Vercel builda e publica; o CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) roda os testes antes disso no PR.

### Limitações conhecidas em produção serverless

- **Filesystem:** `app.py` já detecta a env var `VERCEL` (setada automaticamente pelo Vercel) e usa `/tmp` como `root_dir` do `FilesystemBackend` em vez do diretório do projeto, que é somente leitura em produção. Mas **`/tmp` não é compartilhado nem persistente entre invocações** — um arquivo (ex.: gráfico) gerado numa chamada não estará disponível numa chamada seguinte, mesmo na mesma sessão. Se o agente precisar devolver gráficos/arquivos via API, isso vai exigir mudar a estratégia (ex.: subir para um bucket e retornar a URL) — não implementado aqui.
- **Cold start:** a primeira requisição após um cold start busca o prompt do Langfuse e as tools do MCP de novo (alguns segundos) — ver comentário em `AgentRuntime.startup` em [app.py](app.py). O histórico de conversa em si não é afetado (fica no Postgres, ver **Persistência de conversas** acima).
- **Tamanho do pacote:** as dependências (`pandas`, `langchain`, `psycopg[binary]`, etc.) são razoáveis, mas vale observar o build do Vercel na primeira tentativa por limites de tamanho de função.

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
LANGFUSE_HOST=https://cloud.langfuse.com   # ou https://us.cloud.langfuse.com, conforme a região do projeto

NEON_DATABASE_URL=postgresql://...          # usado para persistência de conversas
```

O Langfuse é obrigatório na configuração atual, porque o prompt de sistema é carregado de lá. `NEON_DATABASE_URL` é obrigatório para rodar `app.py` (API) e para as chamadas do `main_mcp.py` que usam checkpointer — veja **Persistência de conversas** acima.

---

## Uso

### Script de demonstração (CLI)

Contra o servidor MCP publicado:

```bash
python main_mcp.py
```

Contra um servidor MCP local ou alternativo:

```bash
python main_mcp.py http://localhost:8000/mcp
```

A pergunta enviada ao agente está fixa em [main_mcp.py:64](main_mcp.py#L64) — altere-a para testar outros cenários.

### API HTTP (`app.py`)

```bash
python app.py
```

> No Windows, use `python app.py` (não `uvicorn app:app` direto) — veja o comentário no `__main__` de [app.py](app.py) sobre por que o loop de eventos do uvicorn quebra o driver async do Postgres nesse SO. Em produção (Linux/Vercel) isso não é um problema.

Rota principal:

```
POST /chat
{
  "message": "Qual o preço atual da PETR4?",
  "session_id": "opcional — se omitido, um UUID novo é gerado e devolvido na resposta"
}
```

Reenviar o mesmo `session_id` em requisições seguintes mantém o histórico da conversa — a memória vive no Postgres (ver acima), não em RAM, então sobrevive a reinícios do processo.

### Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Os testes (`tests/`) não tocam serviços reais: o agente, o checkpointer e o log de mensagens são mockados/injetados via `app.dependency_overrides` — por isso rodam sem `.env`/credenciais, inclusive no CI.

### CI

[.github/workflows/ci.yml](.github/workflows/ci.yml) roda `pytest` no GitHub Actions em três gatilhos: abertura/novo commit em PR contra `main`, e push direto em `main` (que também cobre merge de PR).

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
| `fastapi` / `uvicorn` | API HTTP do agente (`app.py`) |
| `langgraph-checkpoint-postgres` / `psycopg` | Persistência de conversas no Neon (`persistence.py`) |
| `pytest` / `pytest-asyncio` | Testes unitários |

---

## Aviso

Este projeto é experimental e educacional. As análises são automatizadas, baseadas em dados públicos, e **não constituem recomendação de compra ou venda de ativos**.
