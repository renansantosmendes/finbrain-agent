"""Slash-command shortcuts for the agent's skills.

A free-form message still works exactly as before -- the LLM routes it to a
skill by reading each SKILL.md's `description` (see skills/README.md). These
commands are a deterministic shortcut for the common case: no ambiguity, no
LLM call spent on routing, and predictable behavior for scripted/UI callers.

A command is resolved BEFORE the agent runs (see app.py's /chat handler):
- Unknown command or missing/invalid argument -> a usage message is returned
  directly, without invoking the agent (no LLM cost).
- Known command -> translated into the natural-language instruction that
  triggers the matching skill, then passed to the agent like any other
  message.
- Message doesn't start with "/" -> passed through untouched.
"""
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class CommandSpec:
    usage: str
    """Shown in /help and in error messages. Includes the leading `/name`."""

    description: str
    """One-line summary of what the command does."""

    build_prompt: Callable[[str], str]
    """Turn the raw argument string (text after the command name) into the
    natural-language instruction sent to the agent. Raise `ValueError` with a
    user-facing message if the arguments are missing or malformed."""


def _require_ticker(args: str, command: str) -> str:
    ticker = args.strip()
    if not ticker:
        raise ValueError(f"Uso: `{command} <ticker>` (ex: `{command} PETR4`)")
    if " " in ticker or "," in ticker:
        raise ValueError(f"Informe um único ticker para `{command}` (para comparar vários, use `/compare`).")
    return ticker


def _price(args: str) -> str:
    ticker = _require_ticker(args, "/price")
    return f"Qual o preço atual e o histórico recente da ação {ticker}?"


def _fundamentals(args: str) -> str:
    ticker = _require_ticker(args, "/fundamentals")
    return f"Faça uma análise fundamentalista da ação {ticker}."


def _technical(args: str) -> str:
    ticker = _require_ticker(args, "/technical")
    return f"Faça uma análise técnica (médias móveis, RSI e MACD) da ação {ticker}."


def _compare(args: str) -> str:
    tickers = args.strip()
    if "," not in tickers or len(tickers.split(",")) < 2:
        raise ValueError("Uso: `/compare <ticker1,ticker2,...>` (ex: `/compare PETR4,VALE3,ITUB4`)")
    return f"Compare os seguintes ativos: {tickers}."


def _simulate(args: str) -> str:
    parts = args.split()
    if not parts:
        raise ValueError("Uso: `/simulate <ticker> [days]` (ex: `/simulate MSFT 30`)")
    ticker = parts[0]
    if len(parts) > 1:
        if not parts[1].isdigit():
            raise ValueError("O horizonte em `/simulate <ticker> [days]` deve ser um número de dias (ex: `/simulate MSFT 30`).")
        days = parts[1]
        return f"Simule cenários futuros de preço para a ação {ticker} em um horizonte de {days} dias."
    return f"Simule cenários futuros de preço para a ação {ticker}."


def _crypto(args: str) -> str:
    symbol = args.strip()
    if not symbol:
        raise ValueError("Uso: `/crypto <símbolo>` (ex: `/crypto BTC/USDT` ou `/crypto bitcoin`)")
    return f"Qual o preço atual da criptomoeda {symbol}?"


def _brazil(args: str) -> str:
    indicador = args.strip()
    if not indicador:
        raise ValueError("Uso: `/brazil <indicador>` (ex: `/brazil selic`, `/brazil ipca`, `/brazil dolar`)")
    return f"Qual o valor atual de {indicador} no Brasil?"


def _global(args: str) -> str:
    query = args.strip()
    if not query:
        raise ValueError("Uso: `/global <indicador> <país(es)>` (ex: `/global PIB China`, `/global inflação Brasil,Argentina`)")
    return f"Consulte o seguinte indicador macroeconômico global: {query}."


COMMANDS: dict[str, CommandSpec] = {
    "price": CommandSpec("/price <ticker>", "Preço atual e histórico recente de uma ação.", _price),
    "fundamentals": CommandSpec("/fundamentals <ticker>", "Análise fundamentalista (P/L, ROE, margens, endividamento).", _fundamentals),
    "technical": CommandSpec("/technical <ticker>", "Análise técnica (médias móveis, RSI, MACD).", _technical),
    "compare": CommandSpec("/compare <t1,t2,...>", "Compara dois ou mais ativos lado a lado.", _compare),
    "simulate": CommandSpec("/simulate <ticker> [days]", "Projeção de cenários futuros de preço (GARCH/Monte Carlo).", _simulate),
    "crypto": CommandSpec("/crypto <símbolo>", "Preço atual de uma criptomoeda.", _crypto),
    "brazil": CommandSpec("/brazil <indicador>", "Indicadores macro do Brasil (Selic, IPCA, CDI, dólar).", _brazil),
    "global": CommandSpec("/global <indicador> <país(es)>", "Indicadores macro internacionais (PIB, inflação, desemprego).", _global),
}


def _help_text() -> str:
    lines = ["Comandos disponíveis:", ""]
    for spec in COMMANDS.values():
        lines.append(f"`{spec.usage}` — {spec.description}")
    lines.append("")
    lines.append("Uma mensagem livre (sem `/`) também funciona normalmente.")
    return "\n".join(lines)


@dataclass(frozen=True)
class ResolvedCommand:
    prompt_for_agent: Optional[str]
    """Set when the command should be forwarded to the agent as this prompt."""

    direct_reply: Optional[str]
    """Set when the command is fully handled here (help, usage error, unknown
    command) and the agent should not be invoked at all."""


def resolve(message: str) -> Optional[ResolvedCommand]:
    """Resolve `message` if it's a slash command, else return `None`.

    `None` means "not a command" -- the caller passes `message` to the agent
    untouched, exactly like before commands existed.
    """
    stripped = message.strip()
    if not stripped.startswith("/"):
        return None

    name, _, rest = stripped[1:].partition(" ")
    name = name.lower()

    if name == "help":
        return ResolvedCommand(prompt_for_agent=None, direct_reply=_help_text())

    spec = COMMANDS.get(name)
    if spec is None:
        return ResolvedCommand(
            prompt_for_agent=None,
            direct_reply=f"Comando `/{name}` não reconhecido.\n\n{_help_text()}",
        )

    try:
        prompt = spec.build_prompt(rest)
    except ValueError as e:
        return ResolvedCommand(prompt_for_agent=None, direct_reply=str(e))

    return ResolvedCommand(prompt_for_agent=prompt, direct_reply=None)
