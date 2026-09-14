"""Unit tests for invoices.py: PDF text extraction and the read_credit_card_invoices tool."""
import json

import pytest

import invoices


def _make_pdf_with_text(text: str) -> bytes:
    """Hand-writes a minimal valid single-page PDF with `text` drawn on it
    (pypdf has no page-authoring API, so this is done at the raw object
    level -- good enough for exercising extract_text's happy path)."""
    content = f"BT /F1 24 Tf 100 700 Td ({text}) Tj ET".encode()
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"5 0 obj\n<< /Length %d >>\nstream\n%s\nendstream\nendobj\n" % (len(content), content),
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for obj in objs:
        offsets.append(len(pdf))
        pdf += obj
    xref_offset = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        pdf += ("%010d 00000 n \n" % off).encode()
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref_offset)
    return pdf


@pytest.fixture(autouse=True)
def _reset_invoice_context():
    invoices.clear_current_invoices()
    yield
    invoices.clear_current_invoices()


def test_extract_text_happy_path():
    pdf = _make_pdf_with_text("PETR4 compra R$ 120,00")
    result = invoices.extract_text(pdf, "fatura.pdf")

    assert result.ok is True
    assert result.filename == "fatura.pdf"
    assert "PETR4 compra R$ 120,00" in result.text
    assert result.pages == 1
    assert result.truncated is False
    assert result.error is None


def test_extract_text_rejects_oversized_file():
    oversized = b"0" * (invoices.MAX_FILE_SIZE_BYTES + 1)
    result = invoices.extract_text(oversized, "grande.pdf")

    assert result.ok is False
    assert "10MB" in result.error


def test_extract_text_rejects_invalid_pdf_bytes():
    result = invoices.extract_text(b"isso nao e um pdf", "ruim.pdf")

    assert result.ok is False
    assert result.error is not None


def test_extract_text_truncates_long_text():
    long_text = "gasto " * 2000  # comfortably over MAX_TEXT_CHARS_PER_FILE
    pdf = _make_pdf_with_text(long_text)
    result = invoices.extract_text(pdf, "longa.pdf")

    assert result.ok is True
    assert result.truncated is True
    assert len(result.text) == invoices.MAX_TEXT_CHARS_PER_FILE


def test_read_credit_card_invoices_tool_with_no_files_set():
    output = json.loads(invoices.read_credit_card_invoices.invoke({}))
    assert output["invoices"] == []
    assert "Nenhuma fatura" in output["message"]


def test_read_credit_card_invoices_tool_returns_set_extractions():
    pdf = _make_pdf_with_text("VALE3 venda R$ 50,00")
    extraction = invoices.extract_text(pdf, "fatura.pdf")
    invoices.set_current_invoices([extraction])

    output = json.loads(invoices.read_credit_card_invoices.invoke({}))
    assert len(output["invoices"]) == 1
    assert output["invoices"][0]["filename"] == "fatura.pdf"
    assert "VALE3 venda R$ 50,00" in output["invoices"][0]["text"]
