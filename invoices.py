"""PDF credit card statement intake for the `credit-card-insights` skill.

Design note -- why this isn't a normal MCP-style tool:
Every other tool in this project fetches data from an external API given
small text arguments (a ticker, an indicator code, ...). A PDF's bytes don't
fit that shape: an LLM can't reliably reproduce a binary blob as a tool-call
argument, and even a full base64 round-trip through the model's context
would be wasteful and error-prone. So instead:

1. The PDF bytes arrive out-of-band (multipart upload on /invoices/analyze,
   or a Telegram document) and are extracted to text *before* the agent
   runs -- see app.py's /invoices/analyze and telegram_webhook.
2. `set_current_invoices` stashes that extracted text in a ContextVar for
   the duration of the current request/turn only (ContextVar values don't
   leak across concurrent asyncio tasks, so concurrent requests don't see
   each other's files).
3. `read_credit_card_invoices` is a normal LangChain tool, registered once
   at agent startup like any other tool -- the model calls it (no argument
   needed) to read whatever was stashed in step 2, exactly like it calls
   `collect_yfinance_data` to read a price. This keeps the same
   "skill calls a tool, tool returns JSON, skill's rules interpret it"
   pattern as every other skill instead of a special case.
"""
import io
import json
from contextvars import ContextVar
from dataclasses import asdict, dataclass

from langchain_core.tools import tool
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from logging_config import logger

MAX_FILES = 20
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB -- a statement PDF is a few pages, not a scan dump.
MAX_TEXT_CHARS_PER_FILE = 6_000  # keeps 20 files well within the model's context budget.


@dataclass
class InvoiceExtraction:
    filename: str
    ok: bool
    text: str = ""
    error: str | None = None
    pages: int = 0
    truncated: bool = False


def extract_text(file_bytes: bytes, filename: str) -> InvoiceExtraction:
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        return InvoiceExtraction(
            filename=filename, ok=False,
            error=f"Arquivo maior que o limite de {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB.",
        )

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception:
        logger.exception("invoices: failed to open PDF filename=%s", filename)
        return InvoiceExtraction(filename=filename, ok=False, error="Não foi possível abrir o arquivo como PDF.")

    if reader.is_encrypted:
        # Password-protected statements (common with Brazilian banks, e.g. a
        # PIN derived from the CPF) aren't supported yet -- surface this as a
        # normal extraction failure so the skill can tell the user, instead
        # of the tool silently returning empty text.
        return InvoiceExtraction(
            filename=filename, ok=False,
            error="PDF protegido por senha -- não suportado nesta versão. Remova a senha e envie novamente.",
        )

    try:
        pages_text = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError:
        logger.exception("invoices: failed to extract text filename=%s", filename)
        return InvoiceExtraction(filename=filename, ok=False, error="Falha ao extrair texto do PDF (arquivo corrompido?).")

    full_text = "\n".join(pages_text).strip()
    if not full_text:
        return InvoiceExtraction(
            filename=filename, ok=False, pages=len(reader.pages),
            error="Nenhum texto extraído -- provavelmente é um PDF escaneado (imagem), sem texto selecionável.",
        )

    truncated = len(full_text) > MAX_TEXT_CHARS_PER_FILE
    return InvoiceExtraction(
        filename=filename,
        ok=True,
        text=full_text[:MAX_TEXT_CHARS_PER_FILE],
        pages=len(reader.pages),
        truncated=truncated,
    )


_current_invoices: ContextVar[list[InvoiceExtraction]] = ContextVar("current_invoices", default=[])


def set_current_invoices(extractions: list[InvoiceExtraction]) -> None:
    _current_invoices.set(extractions)


def clear_current_invoices() -> None:
    _current_invoices.set([])


@tool
def read_credit_card_invoices() -> str:
    """Retorna o texto extraído das faturas de cartão de crédito (PDFs) que o usuário enviou nesta mensagem, para análise de gastos.

    Use esta ferramenta sempre que o usuário pedir uma análise de gastos, corte de custos ou tiver enviado arquivo(s) de fatura.
    Se nenhum arquivo foi enviado, o resultado indica isso -- nesse caso, oriente o usuário sobre como enviar (ver skill).
    """
    extractions = _current_invoices.get()
    if not extractions:
        return json.dumps({"invoices": [], "message": "Nenhuma fatura foi enviada nesta mensagem."})

    return json.dumps({"invoices": [asdict(e) for e in extractions]}, ensure_ascii=False)
