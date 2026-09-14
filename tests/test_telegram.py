"""Unit tests for telegram.py's update parsing and session-id derivation."""
import telegram


def test_session_id_for_formats_the_thread_id():
    assert telegram.session_id_for(12345) == "telegram-12345"


def test_extract_incoming_text_uses_sender_id_when_present():
    update = {
        "message": {
            "chat": {"id": 999},  # e.g. a group chat_id
            "from": {"id": 111, "first_name": "Renan"},
            "text": "oi",
        }
    }
    chat_id, user_id, text = telegram.extract_incoming_text(update)
    assert chat_id == 999
    assert user_id == 111
    assert text == "oi"


def test_extract_incoming_text_falls_back_to_chat_id_without_sender():
    """No `from` (e.g. an anonymous channel post) -- chat_id doubles as the
    session key instead of crashing on a missing sender."""
    update = {"message": {"chat": {"id": 555}, "text": "oi"}}
    chat_id, user_id, text = telegram.extract_incoming_text(update)
    assert chat_id == 555
    assert user_id == 555


def test_extract_incoming_text_returns_none_for_non_text_message():
    update = {"message": {"chat": {"id": 555}, "sticker": {"file_id": "abc"}}}
    assert telegram.extract_incoming_text(update) is None


def test_extract_incoming_document_uses_sender_id_when_present():
    update = {
        "message": {
            "chat": {"id": 999},
            "from": {"id": 111},
            "document": {"file_id": "abc", "file_name": "fatura.pdf", "mime_type": "application/pdf"},
            "caption": "minha fatura",
        }
    }
    chat_id, user_id, document, caption = telegram.extract_incoming_document(update)
    assert chat_id == 999
    assert user_id == 111
    assert document["file_id"] == "abc"
    assert caption == "minha fatura"


def test_extract_incoming_document_falls_back_to_chat_id_without_sender():
    update = {
        "message": {
            "chat": {"id": 555},
            "document": {"file_id": "abc"},
        }
    }
    chat_id, user_id, document, caption = telegram.extract_incoming_document(update)
    assert chat_id == 555
    assert user_id == 555
    assert caption == ""


def test_extract_incoming_document_returns_none_without_document():
    update = {"message": {"chat": {"id": 555}, "text": "oi"}}
    assert telegram.extract_incoming_document(update) is None
