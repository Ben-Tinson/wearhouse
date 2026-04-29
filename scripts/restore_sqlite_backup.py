import argparse
import os
import sqlite3
import sys
from typing import Iterable, List, Sequence, Tuple


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DEST = os.path.join(BASE_DIR, "instance", "site.db")
DEFAULT_SOURCE = os.path.join(BASE_DIR, "instance", "site_backup_before_rebuild.db")


# Conservative restore order. Release-linked tables are opt-in because the
# current migration work is focused on release-schema correctness.
SAFE_TABLE_ORDER: List[str] = [
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
            "Restore data from a SQLite backup into the rebuilt site.db "
            "without replacing the destination schema."
        )
    )
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Path to the backup SQLite database.")
    parser.add_argument("--dest", default=DEFAULT_DEST, help="Path to the rebuilt destination SQLite database.")
    parser.add_argument(
        "--include-release-family",
        action="store_true",
        help=(
            "Also restore release, release-linked, wishlist, and sneaker_sale tables. "
            "These tables are audited first and skipped by default."
        ),
    )
    return parser.parse_args()


def connect_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_file(path: str, label: str) -> None:
    if not os.path.exists(path):
        raise SystemExit(f"{label} does not exist: {path}")


def table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    row = conn.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, schema: str, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA {schema}.table_info('{table}')").fetchall()
    return [row["name"] for row in rows]


def row_count(conn: sqlite3.Connection, schema: str, table: str) -> int:
    row = conn.execute(f'SELECT COUNT(*) AS n FROM {schema}."{table}"').fetchone()
    return int(row["n"])


def common_columns(
    conn: sqlite3.Connection, src_schema: str, dest_schema: str, table: str
) -> List[str]:
    src_cols = table_columns(conn, src_schema, table)
    dest_cols = table_columns(conn, dest_schema, table)
    return [col for col in dest_cols if col in src_cols]


def print_count_snapshot(
    conn: sqlite3.Connection,
    table_names: Sequence[str],
    src_schema: str = "src",
    dest_schema: str = "main",
) -> None:
    print("\nRow counts:")
    for table in table_names:
        src_exists = table_exists(conn, src_schema, table)
        dest_exists = table_exists(conn, dest_schema, table)
        src_count = row_count(conn, src_schema, table) if src_exists else "n/a"
        dest_count = row_count(conn, dest_schema, table) if dest_exists else "n/a"
        print(f"  {table}: source={src_count} dest={dest_count}")


def audit_release_family(conn: sqlite3.Connection) -> List[str]:
    issues: List[str] = []

    if not table_exists(conn, "src", "release"):
        issues.append("Source database is missing the release table.")
        return issues

    release_dupes = conn.execute(
        """
        SELECT source, source_product_id, COUNT(*) AS n
        FROM src.release
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

    if table_exists(conn, "src", "release_price"):
        price_dupes = conn.execute(
            """
            SELECT release_id, currency, region, COUNT(*) AS n
            FROM src.release_price
            GROUP BY release_id, currency, region
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        if price_dupes:
            issues.append(
                f"Source release_price table has {len(price_dupes)} duplicate "
                "(release_id, currency, region) rows."
            )

    if table_exists(conn, "src", "release_size_bid"):
        bid_dupes = conn.execute(
            """
            SELECT release_id, size_label, size_type, COUNT(*) AS n
            FROM src.release_size_bid
            GROUP BY release_id, size_label, size_type
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        if bid_dupes:
            issues.append(
                f"Source release_size_bid table has {len(bid_dupes)} duplicate "
                "(release_id, size_label, size_type) rows."
            )

    return issues


def ensure_destination_empty(conn: sqlite3.Connection, table_names: Iterable[str]) -> None:
    non_empty: List[Tuple[str, int]] = []
    for table in table_names:
        if not table_exists(conn, "main", table):
            continue
        count = row_count(conn, "main", table)
        if count > 0:
            non_empty.append((table, count))
    if non_empty:
        lines = ", ".join(f"{table}={count}" for table, count in non_empty)
        raise SystemExit(
            "Destination database is not empty for one or more target tables. "
            f"Aborting restore: {lines}"
        )


def restore_tables(conn: sqlite3.Connection, table_names: Sequence[str]) -> None:
    for table in table_names:
        if not table_exists(conn, "src", table):
            print(f"Skipping {table}: source table missing.")
            continue
        if not table_exists(conn, "main", table):
            raise SystemExit(f"Destination is missing table {table}. Aborting.")

        cols = common_columns(conn, "src", "main", table)
        if not cols:
            raise SystemExit(f"No common columns found for table {table}. Aborting.")

        src_count = row_count(conn, "src", table)
        dest_before = row_count(conn, "main", table)
        col_sql = ", ".join(f'"{col}"' for col in cols)

        if table in FILTERED_DERIVED_TABLES:
            valid_count = valid_filtered_row_count(conn, table)
            skipped_orphans = src_count - valid_count
            print(
                f"Restoring {table}: source={src_count} valid={valid_count} "
                f"skipped_orphans={skipped_orphans} dest_before={dest_before}"
            )
            insert_filtered_rows(conn, table, col_sql)
        else:
            print(f"Restoring {table}: source={src_count} dest_before={dest_before}")
            conn.execute(
                f'INSERT INTO main."{table}" ({col_sql}) '
                f'SELECT {col_sql} FROM src."{table}"'
            )
        dest_after = row_count(conn, "main", table)
        print(f"Restored {table}: dest_after={dest_after}")


def valid_filtered_row_count(conn: sqlite3.Connection, table: str) -> int:
    if table == "step_attribution":
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM src.step_attribution sa
            JOIN src.user u ON u.id = sa.user_id
            JOIN src.sneaker s ON s.id = sa.sneaker_id
            """
        ).fetchone()
        return int(row["n"])
    if table == "sneaker_health_snapshot":
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM src.sneaker_health_snapshot shs
            JOIN src.user u ON u.id = shs.user_id
            JOIN src.sneaker s ON s.id = shs.sneaker_id
            """
        ).fetchone()
        return int(row["n"])
    raise SystemExit(f"Filtered restore is not defined for table {table}.")


def insert_filtered_rows(conn: sqlite3.Connection, table: str, col_sql: str) -> None:
    if table == "step_attribution":
        select_sql = ", ".join(f'sa."{col}"' for col in table_columns(conn, "main", table) if col in table_columns(conn, "src", table))
        conn.execute(
            f"""
            INSERT INTO main."step_attribution" ({col_sql})
            SELECT {select_sql}
            FROM src."step_attribution" sa
            JOIN src.user u ON u.id = sa.user_id
            JOIN src.sneaker s ON s.id = sa.sneaker_id
            """
        )
        return
    if table == "sneaker_health_snapshot":
        select_sql = ", ".join(f'shs."{col}"' for col in table_columns(conn, "main", table) if col in table_columns(conn, "src", table))
        conn.execute(
            f"""
            INSERT INTO main."sneaker_health_snapshot" ({col_sql})
            SELECT {select_sql}
            FROM src."sneaker_health_snapshot" shs
            JOIN src.user u ON u.id = shs.user_id
            JOIN src.sneaker s ON s.id = shs.sneaker_id
            """
        )
        return
    raise SystemExit(f"Filtered restore is not defined for table {table}.")


def main() -> None:
    args = parse_args()
    ensure_file(args.source, "Source database")
    ensure_file(args.dest, "Destination database")

    table_order = list(SAFE_TABLE_ORDER)
    skipped = list(RELEASE_FAMILY_ORDER)

    conn = connect_db(args.dest)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (args.source,))

        print(f"Source: {args.source}")
        print(f"Destination: {args.dest}")

        if args.include_release_family:
            issues = audit_release_family(conn)
            if issues:
                print("\nRelease-family audit failed:")
                for issue in issues:
                    print(f"  - {issue}")
                raise SystemExit(
                    "Release-family restore was requested, but the audit found integrity risks. "
                    "Resolve them before retrying."
                )
            table_order.extend(RELEASE_FAMILY_ORDER)
            skipped = []

        print_count_snapshot(conn, table_order)
        if skipped:
            print("\nSkipping by default:")
            for table in skipped:
                print(f"  - {table}")

        ensure_destination_empty(conn, table_order)

        try:
            conn.execute("BEGIN")
            restore_tables(conn, table_order)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise SystemExit(f"Integrity error during restore: {exc}")
        except Exception:
            conn.rollback()
            raise

        print_count_snapshot(conn, table_order)
        print("\nRestore completed successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        raise
