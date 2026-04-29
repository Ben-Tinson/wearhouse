import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, Iterable, Iterator, List, Sequence, Tuple

import sqlalchemy as sa
from sqlalchemy import MetaData, Table, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.sql.sqltypes import Boolean, Date, DateTime, Integer, Numeric, Time

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import Config


DEFAULT_SOURCE = os.path.join(BASE_DIR, "instance", "site.db")

CORE_TABLE_ORDER: List[str] = [
    "user",
    "exchange_rate",
    "site_schema",
    "sneaker_db",
    "article",
    "user_api_usage",
    "user_api_token",
    "sneaker",
    "article_block",
    "sneaker_note",
    "sneaker_wear",
    "sneaker_clean_event",
    "sneaker_damage_event",
    "sneaker_repair_event",
    "sneaker_repair_resolved_damage",
    "sneaker_expense",
    "step_bucket",
    "step_attribution",
    "exposure_event",
    "sneaker_exposure_attribution",
    "sneaker_health_snapshot",
]

RELEASE_FAMILY_ORDER: List[str] = [
    "release",
    "release_region",
    "release_price",
    "affiliate_offer",
    "release_market_stats",
    "release_size_bid",
    "release_sale_point",
    "release_sales_monthly",
    "wishlist_items",
    "sneaker_sale",
]

FILTERED_DERIVED_TABLES = {"step_attribution", "sneaker_health_snapshot"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import data from the local SQLite database into a staging PostgreSQL "
            "database referenced by DATABASE_URL."
        )
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help="Path to the source SQLite database. Defaults to instance/site.db.",
    )
    parser.add_argument(
        "--skip-release-family",
        action="store_true",
        help="Skip release, wishlist, and sneaker_sale tables.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Rows per insert batch. Defaults to 500.",
    )
    return parser.parse_args()


def ensure_file(path: str, label: str) -> None:
    if not os.path.exists(path):
        raise SystemExit(f"{label} does not exist: {path}")


def connect_sqlite(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_database_url() -> str:
    database_url = Config.SQLALCHEMY_DATABASE_URI
    if not database_url:
        raise SystemExit("DATABASE_URL is not configured.")
    return database_url


def connect_postgres() -> Engine:
    engine = sa.create_engine(get_database_url(), future=True)
    if engine.dialect.name != "postgresql":
        raise SystemExit(
            f"Destination database must be PostgreSQL for this script. "
            f"Resolved dialect: {engine.dialect.name}"
        )
    return engine


def sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def sqlite_table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return [row["name"] for row in rows]


def sqlite_row_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()
    return int(row["n"])


def postgres_row_count(conn: sa.Connection, table: str) -> int:
    return int(conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())


def print_count_snapshot(
    source_conn: sqlite3.Connection,
    dest_conn: sa.Connection,
    table_names: Sequence[str],
) -> None:
    print("\nRow counts:")
    for table in table_names:
        source_count = sqlite_row_count(source_conn, table) if sqlite_table_exists(source_conn, table) else "n/a"
        dest_count = postgres_row_count(dest_conn, table)
        print(f"  {table}: source={source_count} dest={dest_count}")


def ensure_destination_empty(dest_conn: sa.Connection, table_names: Iterable[str]) -> None:
    non_empty: List[Tuple[str, int]] = []
    for table in table_names:
        count = postgres_row_count(dest_conn, table)
        if count > 0:
            non_empty.append((table, count))
    if non_empty:
        lines = ", ".join(f"{table}={count}" for table, count in non_empty)
        raise SystemExit(
            "Destination database is not empty for one or more target tables. "
            f"Aborting import: {lines}"
        )


def audit_release_family(source_conn: sqlite3.Connection) -> List[str]:
    issues: List[str] = []

    release_dupes = source_conn.execute(
        """
        SELECT source, source_product_id, COUNT(*) AS n
        FROM release
        WHERE source IS NOT NULL AND source_product_id IS NOT NULL
        GROUP BY source, source_product_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    if release_dupes:
        issues.append(
            f"Source release table has {len(release_dupes)} duplicate non-null "
            "(source, source_product_id) pairs."
        )

    if sqlite_table_exists(source_conn, "release_price"):
        price_dupes = source_conn.execute(
            """
            SELECT release_id, region, COUNT(*) AS n
            FROM release_price
            GROUP BY release_id, region
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        if price_dupes:
            issues.append(
                f"Source release_price table has {len(price_dupes)} duplicate "
                "(release_id, region) rows under the one-price-per-region rule."
            )

    if sqlite_table_exists(source_conn, "release_size_bid"):
        bid_dupes = source_conn.execute(
            """
            SELECT release_id, size_label, size_type, price_type, COUNT(*) AS n
            FROM release_size_bid
            GROUP BY release_id, size_label, size_type, price_type
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        if bid_dupes:
            issues.append(
                f"Source release_size_bid table has {len(bid_dupes)} duplicate "
                "(release_id, size_label, size_type, price_type) rows."
            )

    return issues


def valid_filtered_row_count(source_conn: sqlite3.Connection, table: str) -> int:
    if table == "step_attribution":
        row = source_conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM step_attribution sa
            JOIN user u ON u.id = sa.user_id
            JOIN sneaker s ON s.id = sa.sneaker_id
            """
        ).fetchone()
        return int(row["n"])
    if table == "sneaker_health_snapshot":
        row = source_conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM sneaker_health_snapshot shs
            JOIN user u ON u.id = shs.user_id
            JOIN sneaker s ON s.id = shs.sneaker_id
            """
        ).fetchone()
        return int(row["n"])
    raise SystemExit(f"Filtered import is not defined for table {table}.")


def reflect_destination_tables(engine: Engine, table_names: Sequence[str]) -> Dict[str, Table]:
    metadata = MetaData()
    metadata.reflect(bind=engine, only=table_names)
    return {table.name: table for table in metadata.sorted_tables}


def iter_source_rows(source_conn: sqlite3.Connection, table: str, columns: Sequence[str]) -> Iterator[sqlite3.Row]:
    col_sql = ", ".join(f'"{col}"' for col in columns)
    if table == "step_attribution":
        query = f"""
            SELECT {", ".join(f'sa."{col}"' for col in columns)}
            FROM step_attribution sa
            JOIN user u ON u.id = sa.user_id
            JOIN sneaker s ON s.id = sa.sneaker_id
        """
        yield from source_conn.execute(query)
        return
    if table == "sneaker_health_snapshot":
        query = f"""
            SELECT {", ".join(f'shs."{col}"' for col in columns)}
            FROM sneaker_health_snapshot shs
            JOIN user u ON u.id = shs.user_id
            JOIN sneaker s ON s.id = shs.sneaker_id
        """
        yield from source_conn.execute(query)
        return
    yield from source_conn.execute(f'SELECT {col_sql} FROM "{table}"')


def convert_value(column: sa.Column[Any], value: Any) -> Any:
    if value is None:
        return None

    column_type = column.type
    if isinstance(column_type, Boolean):
        if isinstance(value, int):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "t", "yes"}:
                return True
            if lowered in {"0", "false", "f", "no"}:
                return False
        return value
    if isinstance(column_type, Numeric):
        return Decimal(str(value))
    if isinstance(column_type, DateTime):
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(column_type, Date):
        return date.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(column_type, Time):
        return time.fromisoformat(value) if isinstance(value, str) else value
    return value


def convert_row(table: Table, row: sqlite3.Row, columns: Sequence[str]) -> Dict[str, Any]:
    return {
        column_name: convert_value(table.c[column_name], row[column_name])
        for column_name in columns
    }


def chunked(items: Iterator[Dict[str, Any]], chunk_size: int) -> Iterator[List[Dict[str, Any]]]:
    batch: List[Dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= chunk_size:
            yield batch
            batch = []
    if batch:
        yield batch


def import_table(
    source_conn: sqlite3.Connection,
    dest_conn: sa.Connection,
    table: Table,
    chunk_size: int,
) -> None:
    source_count = sqlite_row_count(source_conn, table.name)
    dest_before = postgres_row_count(dest_conn, table.name)
    columns = [col.name for col in table.columns if col.name in sqlite_table_columns(source_conn, table.name)]

    if not columns:
        raise SystemExit(f"No common columns found for table {table.name}.")

    if table.name in FILTERED_DERIVED_TABLES:
        valid_count = valid_filtered_row_count(source_conn, table.name)
        skipped_orphans = source_count - valid_count
        print(
            f"Importing {table.name}: source={source_count} valid={valid_count} "
            f"skipped_orphans={skipped_orphans} dest_before={dest_before}"
        )
    else:
        print(f"Importing {table.name}: source={source_count} dest_before={dest_before}")

    source_rows = (
        convert_row(table, row, columns)
        for row in iter_source_rows(source_conn, table.name, columns)
    )
    inserted_rows = 0
    for batch in chunked(source_rows, chunk_size):
        dest_conn.execute(table.insert(), batch)
        inserted_rows += len(batch)

    dest_after = postgres_row_count(dest_conn, table.name)
    print(f"Imported {table.name}: inserted={inserted_rows} dest_after={dest_after}")

    expected_after = valid_filtered_row_count(source_conn, table.name) if table.name in FILTERED_DERIVED_TABLES else source_count
    if dest_after != expected_after:
        raise SystemExit(
            f"Row-count mismatch for {table.name}: expected {expected_after}, found {dest_after}"
        )


def verify_destination_schema(engine: Engine, table_names: Sequence[str]) -> None:
    inspector = inspect(engine)
    missing = sorted(set(table_names) - set(inspector.get_table_names()))
    if missing:
        raise SystemExit(f"Destination database is missing required tables: {', '.join(missing)}")


def sequence_reset_targets(
    dest_conn: sa.Connection,
    tables: Dict[str, Table],
    table_names: Sequence[str],
) -> List[Tuple[Table, str]]:
    targets: List[Tuple[Table, str]] = []
    for table_name in table_names:
        table = tables[table_name]
        primary_key_columns = list(table.primary_key.columns)
        if len(primary_key_columns) != 1:
            continue
        pk_column = primary_key_columns[0]
        if pk_column.name != "id":
            continue
        if not isinstance(pk_column.type, Integer):
            continue
        qualified_table_name = (
            f"{table.schema}.{table.name}" if table.schema else table.name
        )
        sequence_name = dest_conn.execute(
            text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
            {"table_name": qualified_table_name, "column_name": pk_column.name},
        ).scalar_one()
        if not sequence_name:
            continue
        targets.append((table, sequence_name))
    return targets


def reset_postgres_sequences(dest_conn: sa.Connection, targets: Sequence[Tuple[Table, str]]) -> None:
    for table, sequence_name in targets:
        max_id = dest_conn.execute(
            text(f'SELECT COALESCE(MAX(id), 1) FROM "{table.name}"')
        ).scalar_one()
        has_rows = dest_conn.execute(
            text(f'SELECT EXISTS (SELECT 1 FROM "{table.name}")')
        ).scalar_one()

        dest_conn.execute(
            text("SELECT setval(:sequence_name, :max_id, :is_called)"),
            {
                "sequence_name": sequence_name,
                "max_id": int(max_id),
                "is_called": bool(has_rows),
            },
        )
        next_value = int(max_id) + 1 if has_rows else 1
        print(
            f"Reset sequence for {table.name}: sequence={sequence_name} "
            f"max_id={int(max_id)} next_value={next_value}"
        )


def main() -> None:
    args = parse_args()
    ensure_file(args.source, "Source SQLite database")

    table_order = list(CORE_TABLE_ORDER)
    skipped = list(RELEASE_FAMILY_ORDER)
    if not args.skip_release_family:
        audit_conn = connect_sqlite(args.source)
        try:
            issues = audit_release_family(audit_conn)
        finally:
            audit_conn.close()
        if issues:
            print("\nRelease-family audit failed:")
            for issue in issues:
                print(f"  - {issue}")
            raise SystemExit(
                "Staging import aborted because release-family audit found integrity risks. "
                "Resolve them before importing into Postgres."
            )
        table_order.extend(RELEASE_FAMILY_ORDER)
        skipped = []

    source_conn = connect_sqlite(args.source)
    try:
        engine = connect_postgres()
        verify_destination_schema(engine, table_order)
        tables = reflect_destination_tables(engine, table_order)

        print(f"Source SQLite: {args.source}")
        print(f"Destination PostgreSQL: {engine.url.render_as_string(hide_password=True)}")
        if skipped:
            print("\nSkipping release family:")
            for table in skipped:
                print(f"  - {table}")

        with engine.begin() as dest_conn:
            print_count_snapshot(source_conn, dest_conn, table_order)
            ensure_destination_empty(dest_conn, table_order)
            for table_name in table_order:
                import_table(source_conn, dest_conn, tables[table_name], args.chunk_size)
            reset_postgres_sequences(
                dest_conn,
                sequence_reset_targets(dest_conn, tables, table_order),
            )
            print_count_snapshot(source_conn, dest_conn, table_order)
        print("\nSQLite to PostgreSQL import completed successfully.")
    finally:
        source_conn.close()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        raise
