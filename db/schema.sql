-- =====================================================================
-- FinBrain V1 — esquema do banco (Neon / PostgreSQL 16+)
--
-- Tudo vive no schema "finbrain", isolado dos demais projetos que
-- compartilham o banco (coursegen, identity, public, pgl_*). O script é
-- idempotente: pode ser executado mais de uma vez.
--
-- Identidade: users.id é o id da conta no auth_service (claim "sub" do
-- access token). Não há chave estrangeira entre schemas; a conta é criada
-- no primeiro login.
--
-- Mapa tela → tabelas
--   Login / sessão ............ users (perfil, último acesso)
--   Estado da navegação ....... workspaces (aba, ativo, período, médias)
--   Simulações ................ simulation_scenarios
--   Trilho de chamadas ........ tool_calls
--   FinAgent (chat) ........... conversations, messages, tool_calls
--   Histórico antigo do agente  legacy_agent_messages
--
-- Os checkpoints internos do LangGraph (checkpoints, checkpoint_blobs,
-- checkpoint_writes, checkpoint_migrations) ficam neste mesmo schema, criados
-- pelo próprio agente. A conversa é ligada a eles por conversations.thread_id.
-- legacy_agent_messages guarda o histórico antigo do agente, sem dono.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS finbrain;
SET search_path = finbrain, public;

CREATE OR REPLACE FUNCTION finbrain.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- ---------------------------------------------------------------------
-- 1. Usuários
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS finbrain.users (
  id            uuid PRIMARY KEY,                 -- id da conta no auth_service
  email         citext NOT NULL UNIQUE,
  name          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz
);

DROP TRIGGER IF EXISTS users_touch_updated_at ON finbrain.users;
CREATE TRIGGER users_touch_updated_at
  BEFORE UPDATE ON finbrain.users
  FOR EACH ROW EXECUTE FUNCTION finbrain.touch_updated_at();

-- ---------------------------------------------------------------------
-- 2. Simulações salvas (cenários de aporte)
--    Limites espelham app/core/simulation-engine.js (LIMITS).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS finbrain.simulation_scenarios (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                   uuid NOT NULL REFERENCES finbrain.users(id) ON DELETE CASCADE,
  name                      text NOT NULL DEFAULT 'Cenário',
  aporte_mensal             numeric(10,2) NOT NULL DEFAULT 500
                            CHECK (aporte_mensal BETWEEN 100 AND 5000),
  anos                      integer NOT NULL DEFAULT 10
                            CHECK (anos BETWEEN 1 AND 30),
  retorno_anual_pct         numeric(5,2) NOT NULL DEFAULT 5.7
                            CHECK (retorno_anual_pct BETWEEN -2 AND 12),
  volatilidade_anual_pct    numeric(5,2) NOT NULL DEFAULT 14.5
                            CHECK (volatilidade_anual_pct BETWEEN 5 AND 30),
  patrimonio_inicial        numeric(12,2) NOT NULL DEFAULT 0
                            CHECK (patrimonio_inicial BETWEEN 0 AND 100000),
  created_at                timestamptz NOT NULL DEFAULT now(),
  updated_at                timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS simulation_scenarios_user_idx
  ON finbrain.simulation_scenarios (user_id, updated_at DESC);

DROP TRIGGER IF EXISTS simulation_scenarios_touch_updated_at ON finbrain.simulation_scenarios;
CREATE TRIGGER simulation_scenarios_touch_updated_at
  BEFORE UPDATE ON finbrain.simulation_scenarios
  FOR EACH ROW EXECUTE FUNCTION finbrain.touch_updated_at();

-- ---------------------------------------------------------------------
-- 3. Conversas com o FinAgent
--    thread_id é a chave da conversa no LangGraph (AsyncPostgresSaver).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS finbrain.conversations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES finbrain.users(id) ON DELETE CASCADE,
  thread_id        text NOT NULL UNIQUE,
  title            text NOT NULL DEFAULT 'Nova conversa',
  last_message_at  timestamptz,
  archived_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversations_user_idx
  ON finbrain.conversations (user_id, updated_at DESC);

DROP TRIGGER IF EXISTS conversations_touch_updated_at ON finbrain.conversations;
CREATE TRIGGER conversations_touch_updated_at
  BEFORE UPDATE ON finbrain.conversations
  FOR EACH ROW EXECUTE FUNCTION finbrain.touch_updated_at();

-- Mensagens legíveis da conversa, na ordem em que aconteceram.
-- O texto do usuário é gravado depois da redação de PII feita pelo agente.
CREATE TABLE IF NOT EXISTS finbrain.messages (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  conversation_id  uuid NOT NULL REFERENCES finbrain.conversations(id) ON DELETE CASCADE,
  position         integer NOT NULL CHECK (position >= 1),
  role             text NOT NULL CHECK (role IN ('user', 'agent', 'system', 'tool')),
  content          text NOT NULL,
  command          text,
  metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, position)
);

-- ---------------------------------------------------------------------
-- 4. Trilho de chamadas de ferramenta (ativos, FinAgent, macro etc.)
--    Gravado pela UI (aba do painel) e pelo agente (conversation_id).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS finbrain.tool_calls (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id          uuid NOT NULL REFERENCES finbrain.users(id) ON DELETE CASCADE,
  conversation_id  uuid REFERENCES finbrain.conversations(id) ON DELETE SET NULL,
  tool             text NOT NULL,
  source           text NOT NULL,
  params           jsonb NOT NULL DEFAULT '{}'::jsonb,
  result_hash      text,
  called_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tool_calls_user_idx
  ON finbrain.tool_calls (user_id, called_at DESC);
CREATE INDEX IF NOT EXISTS tool_calls_conversation_idx
  ON finbrain.tool_calls (conversation_id)
  WHERE conversation_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- 6. Histórico antigo do agente (agent_conversations.messages)
--    Threads sem dono (modo aberto, Telegram, testes). Mantido só para
--    consulta; novas mensagens vão para finbrain.messages.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS finbrain.legacy_agent_messages (
  id          bigint PRIMARY KEY,
  thread_id   text NOT NULL,
  role        text NOT NULL,
  content     text NOT NULL,
  metadata    jsonb,
  created_at  timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS legacy_agent_messages_thread_idx
  ON finbrain.legacy_agent_messages (thread_id, created_at);

-- ---------------------------------------------------------------------
-- 5. Estado de navegação (uma linha por usuário)
--    Campos espelham app/core/state.js (sym, rng, ma5, ma20, showTable).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS finbrain.workspaces (
  user_id                  uuid PRIMARY KEY REFERENCES finbrain.users(id) ON DELETE CASCADE,
  current_tab              text NOT NULL DEFAULT 'inicio'
                           CHECK (current_tab IN (
                             'inicio', 'dashboards', 'homebroker',
                             'brain', 'simulacoes', 'transparencia'
                           )),
  active_symbol            text NOT NULL DEFAULT 'PETR4'
                           CHECK (active_symbol ~ '^[A-Z0-9./-]{1,20}$'),
  chart_range              text NOT NULL DEFAULT '3M'
                           CHECK (chart_range ~ '^[0-9]{1,3}[A-Z]{1,2}$'),
  show_ma5                 boolean NOT NULL DEFAULT true,
  show_ma20                boolean NOT NULL DEFAULT true,
  show_table               boolean NOT NULL DEFAULT false,
  active_scenario_id       uuid REFERENCES finbrain.simulation_scenarios(id) ON DELETE SET NULL,
  active_conversation_id   uuid REFERENCES finbrain.conversations(id) ON DELETE SET NULL,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS workspaces_touch_updated_at ON finbrain.workspaces;
CREATE TRIGGER workspaces_touch_updated_at
  BEFORE UPDATE ON finbrain.workspaces
  FOR EACH ROW EXECUTE FUNCTION finbrain.touch_updated_at();
