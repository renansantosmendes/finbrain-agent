"""Unit tests for the pure helpers of persistence.py that decide what is stored."""

import pytest

import persistence


@pytest.mark.parametrize(
    "thread_id, owner",
    [
        ("web:ad13-42:0b7e-11", "ad13-42"),
        ("web::0b7e", None),
        ("telegram:123", None),
        ("abc-session", None),
        ("web:ad13:0b7e:extra", None),
    ],
)
def test_owner_is_read_only_from_web_threads(thread_id, owner):
    assert persistence.owner_user_id_of_thread(thread_id) == owner


def test_agent_replies_are_stored_as_agent_role():
    assert persistence.ROLE_BY_LOG_NAME["assistant"] == "agent"
    assert persistence.ROLE_BY_LOG_NAME["user"] == "user"


def test_checkpoints_use_the_finbrain_schema():
    assert persistence.SCHEMA_NAME == "finbrain"
