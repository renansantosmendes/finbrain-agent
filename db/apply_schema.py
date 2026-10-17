"""Apply db/schema.sql to the Neon database, as the owner role.

The schema file is idempotent, so the script can run more than once. It
reads NEON_DATABASE_URL (owner) from the local .env, runs the whole file in
a single transaction and prints the tables it finds in the finbrain schema.

Run it from the project root:

    uv run python db/apply_schema.py
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_NAME = "finbrain"


def read_schema_sql(path: Path) -> str:
    """Read the schema script from disk.

    Parameters
    ----------
    path : Path
        Location of the schema file.

    Returns
    -------
    str
        Full content of the schema file.
    """
    return path.read_text(encoding="utf-8")


def list_tables(connection: psycopg.Connection, schema: str) -> list[str]:
    """List the tables that exist in a schema.

    Parameters
    ----------
    connection : psycopg.Connection
        Open connection to the database.
    schema : str
        Schema whose tables are listed.

    Returns
    -------
    list[str]
        Table names, sorted alphabetically.
    """
    rows = connection.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
        ORDER BY table_name
        """,
        (schema,),
    ).fetchall()
    return [row[0] for row in rows]


def apply_schema(database_url: str, sql: str) -> list[str]:
    """Run the schema script in one transaction and return the new tables.

    Parameters
    ----------
    database_url : str
        Owner connection string of the Neon database.
    sql : str
        Schema script to execute.

    Returns
    -------
    list[str]
        Tables present in the finbrain schema after the script runs.
    """
    with psycopg.connect(database_url) as connection:
        connection.execute(sql)
        tables = list_tables(connection, SCHEMA_NAME)
        connection.commit()
    return tables


def main() -> None:
    """Load the owner URL from .env and apply the schema.

    Returns
    -------
    None
    """
    load_dotenv()
    database_url = os.environ["NEON_DATABASE_URL"]
    tables = apply_schema(database_url, read_schema_sql(SCHEMA_FILE))
    print(f"Schema '{SCHEMA_NAME}' applied. Tables: {', '.join(tables)}")


if __name__ == "__main__":
    main()
