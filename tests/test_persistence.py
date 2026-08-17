"""Unit tests for the pure connection-string logic in persistence.py.

Deliberately doesn't touch the real Neon database -- that's exercised
manually / in integration, not here.
"""
from urllib.parse import parse_qsl, urlsplit

from persistence import SCHEMA_NAME, _with_schema_search_path

POOLED_URL = (
    "postgresql://neondb_owner:secret@ep-noisy-boat-axo08bx1-pooler"
    ".c-4.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
)


def test_drops_pooler_suffix_from_host():
    result = _with_schema_search_path(POOLED_URL)
    host = urlsplit(result).hostname
    assert host == "ep-noisy-boat-axo08bx1.c-4.us-east-2.aws.neon.tech"


def test_sets_search_path_to_target_schema_with_public_fallback():
    result = _with_schema_search_path(POOLED_URL)
    query = dict(parse_qsl(urlsplit(result).query))
    assert query["options"] == f"-csearch_path={SCHEMA_NAME},public"


def test_accepts_custom_schema_name():
    result = _with_schema_search_path(POOLED_URL, schema="custom_schema")
    query = dict(parse_qsl(urlsplit(result).query))
    assert query["options"] == "-csearch_path=custom_schema,public"


def test_preserves_credentials_and_database_path():
    result = _with_schema_search_path(POOLED_URL)
    parts = urlsplit(result)
    assert parts.username == "neondb_owner"
    assert parts.password == "secret"
    assert parts.path == "/neondb"


def test_preserves_existing_query_params():
    result = _with_schema_search_path(POOLED_URL)
    query = dict(parse_qsl(urlsplit(result).query))
    assert query["sslmode"] == "require"
    assert query["channel_binding"] == "require"


def test_leaves_url_without_pooler_suffix_unchanged_host():
    url = "postgresql://user:pass@ep-plain-host.aws.neon.tech/db"
    result = _with_schema_search_path(url)
    assert urlsplit(result).hostname == "ep-plain-host.aws.neon.tech"
