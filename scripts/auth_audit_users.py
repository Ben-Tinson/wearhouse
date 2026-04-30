"""Read-only auth audit for the Soletrak ``user`` and ``user_api_token`` tables.

Phase 1 of the Supabase Auth migration. Surfaces data hazards that would
corrupt linkage or risk admin lockout in Phase 2/3.

The script writes nothing to the database, makes no network calls, and does
not import any Supabase SDK. Run it before any backfill and re-run it
immediately before any rollout phase.

Usage::

    python scripts/auth_audit_users.py
    python scripts/auth_audit_users.py --format json
    python scripts/auth_audit_users.py --output audit.json --format json

Exit codes:

    0  clean (no blocking hazards; warnings allowed)
    1  at least one blocking hazard present
    2  the script itself failed (DB unreachable, etc.)

Severity levels used in the report:

    info     baseline counts
    warning  worth acknowledging in the Phase 2 design
    blocking must be resolved before identity backfill begins
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from sqlalchemy import func

from app import create_app
from extensions import db
from models import User, UserApiToken


SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_BLOCKING = "blocking"


# ---------------------------------------------------------------------------
# Check implementations. Each returns a dict with stable keys so the script's
# output format does not depend on the order of iteration.
# ---------------------------------------------------------------------------


def _row_summary(user: User) -> Dict[str, Any]:
    return {
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "is_admin": bool(user.is_admin),
        "is_email_confirmed": bool(user.is_email_confirmed),
        "pending_email": user.pending_email,
    }


def check_c1_baseline_counts(session) -> Dict[str, Any]:
    total_users = session.query(func.count(User.id)).scalar() or 0
    total_admins = (
        session.query(func.count(User.id)).filter(User.is_admin.is_(True)).scalar() or 0
    )
    total_confirmed = (
        session.query(func.count(User.id))
        .filter(User.is_email_confirmed.is_(True))
        .scalar()
        or 0
    )
    total_pending = (
        session.query(func.count(User.id))
        .filter(User.pending_email.isnot(None))
        .scalar()
        or 0
    )
    active_tokens = (
        session.query(func.count(UserApiToken.id))
        .filter(UserApiToken.revoked_at.is_(None))
        .scalar()
        or 0
    )
    return {
        "id": "C1",
        "severity": SEVERITY_INFO,
        "label": "baseline counts",
        "count": total_users,
        "details": {
            "users_total": total_users,
            "users_admin": total_admins,
            "users_email_confirmed": total_confirmed,
            "users_with_pending_email": total_pending,
            "user_api_tokens_active": active_tokens,
        },
    }


def check_c2_email_case_collisions(session) -> Dict[str, Any]:
    """Two or more users whose emails differ only by case."""
    rows = (
        session.query(func.lower(User.email).label("normalised"), func.count(User.id))
        .group_by(func.lower(User.email))
        .having(func.count(User.id) > 1)
        .all()
    )
    examples: List[Dict[str, Any]] = []
    for normalised, _count in rows:
        users = (
            session.query(User)
            .filter(func.lower(User.email) == normalised)
            .order_by(User.id)
            .all()
        )
        examples.append(
            {
                "normalised_email": normalised,
                "rows": [_row_summary(u) for u in users],
            }
        )
    return {
        "id": "C2",
        "severity": SEVERITY_BLOCKING,
        "label": "case-collision emails",
        "count": len(examples),
        "rows": examples,
    }


def check_c3_duplicate_usernames(session) -> Dict[str, Any]:
    """Defensive: column is ``unique=True`` but verify no historic violation."""
    rows = (
        session.query(User.username, func.count(User.id))
        .group_by(User.username)
        .having(func.count(User.id) > 1)
        .all()
    )
    examples = [{"username": username, "count": count} for username, count in rows]
    return {
        "id": "C3",
        "severity": SEVERITY_BLOCKING,
        "label": "duplicate usernames",
        "count": len(examples),
        "rows": examples,
    }


def check_c4_empty_email(session) -> Dict[str, Any]:
    users = (
        session.query(User)
        .filter((User.email.is_(None)) | (func.trim(User.email) == ""))
        .order_by(User.id)
        .all()
    )
    return {
        "id": "C4",
        "severity": SEVERITY_BLOCKING,
        "label": "users with empty / whitespace email",
        "count": len(users),
        "rows": [_row_summary(u) for u in users],
    }


def check_c5_empty_username(session) -> Dict[str, Any]:
    users = (
        session.query(User)
        .filter((User.username.is_(None)) | (func.trim(User.username) == ""))
        .order_by(User.id)
        .all()
    )
    return {
        "id": "C5",
        "severity": SEVERITY_WARNING,
        "label": "users with empty / whitespace username",
        "count": len(users),
        "rows": [_row_summary(u) for u in users],
    }


def check_c6_unconfirmed_users(session) -> Dict[str, Any]:
    total = (
        session.query(func.count(User.id))
        .filter(User.is_email_confirmed.is_(False))
        .scalar()
        or 0
    )
    unconfirmed_admins = (
        session.query(func.count(User.id))
        .filter(User.is_email_confirmed.is_(False), User.is_admin.is_(True))
        .scalar()
        or 0
    )
    return {
        "id": "C6",
        "severity": SEVERITY_WARNING,
        "label": "users with is_email_confirmed=False",
        "count": int(total),
        "details": {
            "unconfirmed_total": int(total),
            "unconfirmed_admins": int(unconfirmed_admins),
        },
    }


def check_c7_unconfirmed_admins(session) -> Dict[str, Any]:
    users = (
        session.query(User)
        .filter(User.is_email_confirmed.is_(False), User.is_admin.is_(True))
        .order_by(User.id)
        .all()
    )
    return {
        "id": "C7",
        "severity": SEVERITY_BLOCKING,
        "label": "unconfirmed admins",
        "count": len(users),
        "rows": [_row_summary(u) for u in users],
    }


def check_c8_pending_email_rows(session) -> Dict[str, Any]:
    users = (
        session.query(User)
        .filter(User.pending_email.isnot(None))
        .order_by(User.id)
        .all()
    )
    return {
        "id": "C8",
        "severity": SEVERITY_WARNING,
        "label": "users with non-null pending_email",
        "count": len(users),
        "rows": [_row_summary(u) for u in users],
    }


def check_c9_pending_email_collisions(session) -> Dict[str, Any]:
    """A row's ``pending_email`` matches a different row's ``email`` (case-insensitive)."""
    pending_users = (
        session.query(User)
        .filter(User.pending_email.isnot(None))
        .order_by(User.id)
        .all()
    )
    collisions: List[Dict[str, Any]] = []
    for u in pending_users:
        if not u.pending_email:
            continue
        target = u.pending_email.strip().lower()
        if not target:
            continue
        other = (
            session.query(User)
            .filter(func.lower(User.email) == target, User.id != u.id)
            .first()
        )
        if other is not None:
            collisions.append(
                {
                    "pending": _row_summary(u),
                    "claimed_by": _row_summary(other),
                }
            )
    return {
        "id": "C9",
        "severity": SEVERITY_BLOCKING,
        "label": "pending_email collides with another user's email",
        "count": len(collisions),
        "rows": collisions,
    }


def check_c10_unexpected_supabase_links(session) -> Dict[str, Any]:
    """Linked-user count.

    Phase 1 used this as a blocking sentinel (no row should be linked yet).
    Phase 2 downgrades it to ``info`` because admins are now intentionally
    pre-linked via ``scripts/link_supabase_identities.py``. We still report
    the count and rows so operators can see at a glance how many users have
    been linked, but the audit no longer blocks on it.
    """
    column = getattr(User, "supabase_auth_user_id", None)
    if column is None:
        # Migration not applied yet. Surface as warning rather than blocking;
        # the script is still useful pre-migration as a baseline.
        return {
            "id": "C10",
            "severity": SEVERITY_WARNING,
            "label": "supabase_auth_user_id column not present (migration not applied?)",
            "count": 0,
            "rows": [],
        }
    users = (
        session.query(User)
        .filter(column.isnot(None))
        .order_by(User.id)
        .all()
    )
    return {
        "id": "C10",
        "severity": SEVERITY_INFO,
        "label": "users linked to a Supabase Auth identity",
        "count": len(users),
        "rows": [_row_summary(u) for u in users],
    }


def check_c11_orphan_api_tokens(session) -> Dict[str, Any]:
    """``UserApiToken`` rows whose ``user_id`` does not resolve to a User."""
    rows = (
        session.query(UserApiToken)
        .outerjoin(User, UserApiToken.user_id == User.id)
        .filter(User.id.is_(None))
        .all()
    )
    return {
        "id": "C11",
        "severity": SEVERITY_BLOCKING,
        "label": "user_api_token rows with no matching user",
        "count": len(rows),
        "rows": [
            {
                "token_id": t.id,
                "user_id": t.user_id,
                "name": t.name,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "revoked_at": t.revoked_at.isoformat() if t.revoked_at else None,
            }
            for t in rows
        ],
    }


def check_c12_active_token_distribution(session) -> Dict[str, Any]:
    """Top 10 users by active (non-revoked) ``UserApiToken`` count."""
    rows = (
        session.query(
            UserApiToken.user_id,
            func.count(UserApiToken.id).label("active_count"),
        )
        .filter(UserApiToken.revoked_at.is_(None))
        .group_by(UserApiToken.user_id)
        .order_by(func.count(UserApiToken.id).desc())
        .limit(10)
        .all()
    )
    return {
        "id": "C12",
        "severity": SEVERITY_INFO,
        "label": "top users by active UserApiToken count",
        "count": len(rows),
        "rows": [{"user_id": user_id, "active_count": int(count)} for user_id, count in rows],
    }


CHECKS = [
    check_c1_baseline_counts,
    check_c2_email_case_collisions,
    check_c3_duplicate_usernames,
    check_c4_empty_email,
    check_c5_empty_username,
    check_c6_unconfirmed_users,
    check_c7_unconfirmed_admins,
    check_c8_pending_email_rows,
    check_c9_pending_email_collisions,
    check_c10_unexpected_supabase_links,
    check_c11_orphan_api_tokens,
    check_c12_active_token_distribution,
]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _db_project_ref(uri: Optional[str]) -> Optional[str]:
    if not uri:
        return None
    # Supabase Postgres host names look like ``db.<ref>.supabase.co``.
    for token in uri.replace("@", " ").replace("/", " ").split():
        if ".supabase.co" in token and token.startswith("db."):
            parts = token.split(".")
            if len(parts) >= 3:
                return parts[1]
    return None


def run_checks(session) -> List[Dict[str, Any]]:
    return [check_fn(session) for check_fn in CHECKS]


def summarise(results: List[Dict[str, Any]]) -> Dict[str, int]:
    summary = {SEVERITY_INFO: 0, SEVERITY_WARNING: 0, SEVERITY_BLOCKING: 0}
    for result in results:
        severity = result.get("severity", SEVERITY_INFO)
        triggered = bool(result.get("count", 0))
        if severity == SEVERITY_INFO:
            summary[SEVERITY_INFO] += 1
        elif triggered and severity == SEVERITY_WARNING:
            summary[SEVERITY_WARNING] += 1
        elif triggered and severity == SEVERITY_BLOCKING:
            summary[SEVERITY_BLOCKING] += 1
    return summary


def exit_code_from_summary(summary: Dict[str, int]) -> int:
    return 1 if summary.get(SEVERITY_BLOCKING, 0) > 0 else 0


def render_text(
    results: List[Dict[str, Any]],
    summary: Dict[str, int],
    db_uri: Optional[str],
    project_ref: Optional[str],
) -> str:
    out: List[str] = []
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out.append(f"=== Soletrak Auth Audit — {generated_at} ===")
    if db_uri:
        out.append(f"DB: {db_uri}")
    if project_ref:
        out.append(f"Project ref: {project_ref}")
    out.append("")

    for result in results:
        severity = result.get("severity", SEVERITY_INFO)
        count = result.get("count", 0)
        triggered = bool(count)
        if severity == SEVERITY_INFO:
            tag = "[INFO]"
        elif triggered and severity == SEVERITY_BLOCKING:
            tag = "[BLOCK]"
        elif triggered and severity == SEVERITY_WARNING:
            tag = "[WARN]"
        else:
            tag = "[OK]"
        out.append(
            f"{tag:<7} {result['id']:<4} {result['label']:<55} count={count}"
        )
        details = result.get("details")
        if details:
            for k, v in details.items():
                out.append(f"           - {k}: {v}")
        rows = result.get("rows") or []
        if rows and severity != SEVERITY_INFO:
            preview = rows[:5]
            for row in preview:
                out.append(f"           - {json.dumps(row, default=str)}")
            if len(rows) > len(preview):
                out.append(f"           - ... ({len(rows) - len(preview)} more)")

    out.append("")
    out.append(
        f"Summary: {summary[SEVERITY_BLOCKING]} blocking, "
        f"{summary[SEVERITY_WARNING]} warning, {summary[SEVERITY_INFO]} info."
    )
    out.append(f"Exit code: {exit_code_from_summary(summary)}")
    return "\n".join(out)


def render_json(
    results: List[Dict[str, Any]],
    summary: Dict[str, int],
    db_uri: Optional[str],
    project_ref: Optional[str],
) -> str:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "db_uri": db_uri,
        "db_project_ref": project_ref,
        "checks": results,
        "summary": summary,
        "exit_code": exit_code_from_summary(summary),
    }
    return json.dumps(payload, default=str, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only auth audit for the Soletrak user and user_api_token tables.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write the report to PATH instead of stdout.",
    )
    return parser.parse_args(argv)


def render_report(
    session,
    *,
    output_format: str = "text",
    db_uri: Optional[str] = None,
) -> Tuple[int, str]:
    """Run all checks and render a report for the given session.

    Returns ``(exit_code, rendered_report)``. Used by both the CLI and the
    test suite so tests do not need a subprocess.
    """
    results = run_checks(session)
    summary = summarise(results)
    project_ref = _db_project_ref(db_uri)
    if output_format == "json":
        rendered = render_json(results, summary, db_uri, project_ref)
    else:
        rendered = render_text(results, summary, db_uri, project_ref)
    return exit_code_from_summary(summary), rendered


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        app = create_app()
        with app.app_context():
            db_uri = app.config.get("SQLALCHEMY_DATABASE_URI")
            exit_code, rendered = render_report(
                db.session, output_format=args.format, db_uri=db_uri
            )
    except Exception as exc:  # pragma: no cover - top-level safety net
        print(f"auth_audit_users: failed: {exc}", file=sys.stderr)
        return 2

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(rendered)
            if not rendered.endswith("\n"):
                fh.write("\n")
    else:
        print(rendered)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
