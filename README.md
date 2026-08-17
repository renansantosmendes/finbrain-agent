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

### Middlewares de produção

`app.py` (`_build_middleware`) adiciona ao agente, além do que o `create_deep_agent` já inclui por padrão (`TodoListMiddleware`, `SkillsMiddleware`, `SummarizationMiddleware` própria do deepagents, etc.):

| Middleware | Por quê |
|---|---|
| `ToolErrorMiddleware` | Converte exceção de tool (ex.: ticker inválido) em `ToolMessage` de erro pro modelo reagir, em vez de derrubar a run com 500. |
| `ToolRetryMiddleware` | As tools vêm de um servidor MCP remoto por HTTP — retry com backoff antes de desistir (`on_failure="error"` para a exceção chegar no `ToolErrorMiddleware` acima). |
| `ModelFallbackMiddleware` | Troca pra `openai:gpt-4o-mini` se `gpt-5-nano`/OpenAI falhar — o agente é a única via de resposta da API. |
| `ModelCallLimitMiddleware` / `ToolCallLimitMiddleware` | Teto de chamadas de modelo/tool por execução — proteção contra loop descontrolado e custo inesperado. |
| `PIIMiddleware` (`email`, `credit_card`) | Redige PII do input do usuário antes de seguir pro modelo/Langfuse. |
| `ContextEditingMiddleware` | Poda resultados antigos de tool do histórico (ex.: JSON grande da simulação GARCH) pra não inflar contexto/custo em sessões longas. |

> **Gap conhecido:** `PIIMiddleware` redige o conteúdo que chega ao modelo e ao Langfuse, mas `persistence.log_message` grava a mensagem do usuário **antes** do agente rodar — ou seja, o texto original (com PII) ainda vai em texto puro pra tabela `agent_conversations.messages`. Não corrigido aqui; se isso importa pro seu caso, mova o log da mensagem do usuário para depois da execução do agente, lendo `result["messages"]` (já redigido) em vez de `request.message`.

---

## Estrutura do repositório

```
finbrain-agent/
├── main_mcp.py           # script de demonstração: roda o agente uma vez via CLI
├── app.py                # API FastAPI (rota /chat) — também o entrypoint que o Vercel detecta
├── vercel.json            # config da função serverless (maxDuration, excludeFiles)
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

A API (`app.py`) está pronta para deploy como função serverless Python no Vercel usando o runtime Python **zero-config**: o Vercel detecta automaticamente uma instância `FastAPI` chamada `app` em `app.py` (ou `index.py`/`server.py`/`main.py`) na raiz do projeto — não é preciso pasta `api/` nem `rewrites` manuais.

- **`app.py`** — já expõe `app = FastAPI(..., lifespan=lifespan)` na raiz; é o próprio entrypoint. Startup/shutdown do FastAPI (`AgentRuntime.startup`/`shutdown`) são suportados nativamente pelo runtime.
- **[vercel.json](vercel.json)** — configura a função resolvida (`app.py`): `maxDuration: 180`, e `excludeFiles` para não empacotar `tests/`, `.github/` etc. no bundle da função. Com Fluid Compute (padrão hoje), o próprio Hobby já tem default/máximo de **300s** — 180s dá margem para cold start (bootstrap do schema + prompt do Langfuse + tools do MCP + conexão do checkpointer, tudo sequencial em `AgentRuntime.startup`) mais uma resposta com tool calls e reasoning, sem se aproximar do teto do plano. **Não abaixe isso sem medir**: um valor menor aqui é um timeout que a própria Vercel não te daria de graça.
- **[.python-version](.python-version)** — fixa `3.12` (as versões suportadas atualmente pelo Vercel são 3.12, 3.13 e 3.14 — confira em [runtimes Python do Vercel](https://vercel.com/docs/functions/runtimes/python) se ainda está atual no momento do deploy).
- **[.vercelignore](.vercelignore)** — exclui `.venv/`, `tests/`, `.github/`, `.env` etc. do pacote enviado.

> Já existiu aqui uma estrutura com `api/index.py` + `rewrites` no `vercel.json` — isso é o padrão **legado** do runtime Python do Vercel (pasta `/api` com roteamento por arquivo) e **conflita** com a auto-detecção do `app.py` na raiz, causando deploys que não respondem a nenhuma rota e não geram log algum. Foi removido — use só `app.py` na raiz.

### Passos

1. Importe o repositório no [dashboard do Vercel](https://vercel.com/new) (framework preset: "Other").
2. Configure as variáveis de ambiente do projeto (Settings → Environment Variables) com os mesmos valores do `.env` local: `OPENAI_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `NEON_DATABASE_URL`.
3. Deploy. A cada push, o Vercel builda e publica; o CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) roda os testes antes disso no PR.

### Limitações conhecidas em produção serverless

- **Filesystem:** `root_dir` do `FilesystemBackend` é sempre o diretório absoluto do projeto (ver comentário em `app.py`) — necessário para `skills=["skills"]` ser encontrado. Consequência: nenhuma tool pode *escrever* arquivo em produção (só `/tmp` é gravável no Vercel, e não é essa a pasta usada). Só importa se/quando uma tool de escrita for adicionada.
- **Cold start:** a primeira requisição após um cold start busca o prompt do Langfuse e as tools do MCP de novo, além de abrir a conexão do checkpointer no endpoint *unpooled* do Neon (mais lenta que a poolada — ver [persistence.py](persistence.py) sobre por que não dá pra usar o pooler aqui). Isso soma alguns segundos a dezenas de segundos antes mesmo do agente começar a responder. O histórico de conversa em si não é afetado (fica no Postgres, ver **Persistência de conversas** acima).
- **Timeout:** se a função for morta por exceder `maxDuration`, o log da execução pode não aparecer no Runtime Logs (o processo é encerrado no meio, sem tempo de flush) — se isso acontecer, cheque a aba **Observability/Logs** do dashboard (não só o tail ao vivo) e considere aumentar `maxDuration` (dentro do teto do plano) antes de investigar mais fundo.
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
