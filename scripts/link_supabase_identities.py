"""Admin linkage CLI — Phase 2 of the Supabase Auth migration.

This script is the **only sanctioned path** for populating
``user.supabase_auth_user_id`` in production. The resolver and decorators
must never write to that column (per ``docs/DECISIONS.md``).

Operating principles:

    - Dry-run by default. ``--apply`` is required for any DB or
      Supabase admin API mutation.
    - Phase 2 scope is admin users only. The CLI requires either
      ``--admins-only`` or ``--user-id <id>`` so a broad accidental
      run cannot happen.
    - Every applied action writes a structured JSONL audit row to
      ``backups/auth/supabase_link_audit_<timestamp>.jsonl``.
    - Reversible via ``--unlink --user-id <id> --apply``.
    - Idempotent: re-running ``--apply`` for an already-linked admin
      is a no-op (recorded as ``noop`` in the audit log, never as
      an error).

Usage::

    # Dry-run: show what would happen for every unlinked admin.
    python scripts/link_supabase_identities.py --admins-only

    # Dry-run for a single user.
    python scripts/link_supabase_identities.py --user-id 42

    # Apply: create / link Supabase identities for all admins, plus
    # send each new admin a Supabase password-reset email.
    python scripts/link_supabase_identities.py \\
        --admins-only --apply --send-onboarding

    # Reverse a single link (must include --apply).
    python scripts/link_supabase_identities.py \\
        --unlink --user-id 42 --apply

Exit codes::

    0  clean run (no errors; dry-run with no blockers).
    1  at least one user blocked or errored during apply.
    2  invalid CLI arguments / missing config.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import create_app
from extensions import db
from models import User
from services.supabase_auth_linkage import (
    AppUserAlreadyLinked,
    AppUserNotFound,
    LinkageError,
    SupabaseIdentityAlreadyLinked,
    link_app_user_to_supabase,
    unlink_app_user,
)
from services.supabase_auth_service import SupabaseAdminClient, SupabaseAdminError


DEFAULT_AUDIT_DIR = os.path.join(BASE_DIR, "backups", "auth")


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _generate_audit_path(audit_dir: str) -> str:
    Path(audit_dir).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(audit_dir, f"supabase_link_audit_{stamp}.jsonl")


def _audit_record(
    audit_path: Optional[str],
    *,
    action: str,
    user: User,
    args: argparse.Namespace,
    supabase_uuid: Optional[str] = None,
    error: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "timestamp": _now_iso(),
        "action": action,
        "app_user_id": user.id,
        "email": user.email,
        "is_admin": bool(user.is_admin),
        "supabase_uuid": str(supabase_uuid) if supabase_uuid else None,
        "dry_run": dry_run,
        "by_admin": False,
        "source": "cli",
        "send_onboarding": bool(getattr(args, "send_onboarding", False)),
    }
    if error:
        entry["error"] = error
    if audit_path:
        Path(os.path.dirname(audit_path)).mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    return entry


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 2 admin linkage CLI for Supabase Auth.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform mutations. Without this flag, the script is read-only.",
    )
    parser.add_argument(
        "--admins-only",
        action="store_true",
        dest="admins_only",
        help="Restrict to is_admin=True users (required in Phase 2 unless --user-id given).",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        dest="user_id",
        help="Operate on a single user id only.",
    )
    parser.add_argument(
        "--unlink",
        action="store_true",
        help="Unlink mode: clear user.supabase_auth_user_id. Requires --user-id.",
    )
    parser.add_argument(
        "--send-onboarding",
        action="store_true",
        dest="send_onboarding",
        help="After linking, trigger a Supabase password-reset email so the admin sets a Supabase password.",
    )
    parser.add_argument(
        "--audit-dir",
        default=DEFAULT_AUDIT_DIR,
        dest="audit_dir",
        help="Directory for the audit JSONL file (defaults to backups/auth).",
    )
    parser.add_argument(
        "--audit-path",
        default=None,
        dest="audit_path",
        help="Explicit audit JSONL path (overrides --audit-dir; primarily for tests).",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        dest="no_audit",
        help="Suppress audit-file writes (tests / dry-run only).",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> Optional[str]:
    """Return an error message if the argument combination is invalid, else None."""
    if args.unlink:
        if args.user_id is None:
            return "--unlink requires --user-id <id>"
        if args.admins_only:
            return "--unlink does not accept --admins-only; use --user-id <id> explicitly"
        if args.send_onboarding:
            return "--send-onboarding has no meaning with --unlink"
        return None

    if args.admins_only and args.user_id is not None:
        return "use either --admins-only or --user-id, not both"
    if not args.admins_only and args.user_id is None:
        return "Phase 2 requires --admins-only or --user-id <id> for explicit scope"
    return None


# ---------------------------------------------------------------------------
# Core operations (callable from tests; do not call sys.exit)
# ---------------------------------------------------------------------------


def _candidate_users(args: argparse.Namespace) -> List[User]:
    if args.user_id is not None:
        user = db.session.get(User, args.user_id)
        return [user] if user is not None else []
    return User.query.filter(User.is_admin.is_(True)).order_by(User.id).all()


def _classify_link_action(
    user: User,
    client: Optional[SupabaseAdminClient],
) -> Dict[str, Any]:
    """Pure classification — does not mutate anything."""
    if user.supabase_auth_user_id is not None:
        return {"kind": "noop", "reason": "already linked"}
    if not user.email or not user.email.strip():
        return {"kind": "blocked", "reason": "user has no email"}
    if user.is_admin and not user.is_email_confirmed:
        return {"kind": "blocked", "reason": "admin email is not confirmed"}

    if client is not None:
        try:
            existing = client.get_user_by_email(user.email)
        except SupabaseAdminError as exc:
            return {"kind": "blocked", "reason": f"supabase lookup failed: {exc}"}
        if existing and existing.get("id"):
            return {"kind": "would_link_existing", "supabase_uuid": existing["id"]}

    return {"kind": "would_create_and_link"}


def run_link(
    args: argparse.Namespace,
    *,
    client: Optional[SupabaseAdminClient] = None,
    audit_path: Optional[str] = None,
    output=sys.stdout,
) -> int:
    """Run link mode. Returns an exit code."""
    candidates = _candidate_users(args)

    effective_audit_path: Optional[str] = None
    if not args.no_audit:
        effective_audit_path = audit_path or args.audit_path
        if args.apply and effective_audit_path is None:
            effective_audit_path = _generate_audit_path(args.audit_dir)

    plans: List[Tuple[User, Dict[str, Any]]] = [
        (user, _classify_link_action(user, client)) for user in candidates
    ]

    print("=== Soletrak Supabase linkage CLI ===", file=output)
    scope = "admins-only" if args.admins_only else f"user-id={args.user_id}"
    print(f"mode: {'APPLY' if args.apply else 'DRY-RUN'}, scope: {scope}", file=output)
    print(f"candidates: {len(plans)}", file=output)
    print("", file=output)

    blocked = 0
    skipped = 0
    for user, plan in plans:
        kind = plan["kind"]
        prefix = f"  user_id={user.id} email={user.email} is_admin={user.is_admin}: "
        if kind == "noop":
            print(prefix + "already linked, skipping", file=output)
            skipped += 1
        elif kind == "would_link_existing":
            print(prefix + f"would link to existing Supabase id {plan['supabase_uuid']}", file=output)
        elif kind == "would_create_and_link":
            if client is None:
                print(prefix + "would create new Supabase identity and link (offline dry-run)", file=output)
            else:
                print(prefix + "would create new Supabase identity and link", file=output)
        elif kind == "blocked":
            print(prefix + f"BLOCKED: {plan['reason']}", file=output)
            blocked += 1

    if not args.apply:
        print("", file=output)
        print(
            f"Dry-run summary: {len(plans)} candidates, {skipped} already linked, {blocked} blocked.",
            file=output,
        )
        print("Pass --apply to perform mutations.", file=output)
        return 1 if blocked else 0

    if client is None:
        print("--apply requires a configured Supabase admin client.", file=sys.stderr)
        return 2

    print("", file=output)
    print("Applying...", file=output)
    applied = 0
    errors = 0
    for user, plan in plans:
        kind = plan["kind"]
        if kind == "noop":
            _audit_record(effective_audit_path, action="noop", user=user, args=args)
            continue
        if kind == "blocked":
            _audit_record(
                effective_audit_path,
                action="error",
                user=user,
                args=args,
                error=plan["reason"],
            )
            errors += 1
            continue
        try:
            if kind == "would_link_existing":
                supabase_uuid = plan["supabase_uuid"]
            else:
                created = client.create_user(user.email, email_confirm=True)
                supabase_uuid = created.get("id")
                if not supabase_uuid:
                    raise SupabaseAdminError("create_user response missing 'id'")
            link_app_user_to_supabase(user.id, supabase_uuid, by_admin=False, source="cli")
            applied += 1
            print(f"  linked user_id={user.id} → {supabase_uuid}", file=output)
            _audit_record(
                effective_audit_path,
                action="link",
                user=user,
                args=args,
                supabase_uuid=supabase_uuid,
            )
            if args.send_onboarding:
                try:
                    client.send_recovery_link(user.email)
                except SupabaseAdminError as exc:
                    # Recovery email failure is logged but does not undo the link.
                    print(f"    onboarding email failed for user_id={user.id}: {exc}", file=output)
                    _audit_record(
                        effective_audit_path,
                        action="onboarding_error",
                        user=user,
                        args=args,
                        supabase_uuid=supabase_uuid,
                        error=str(exc),
                    )
        except (LinkageError, SupabaseAdminError) as exc:
            errors += 1
            print(f"  ERROR linking user_id={user.id}: {exc}", file=output)
            _audit_record(
                effective_audit_path,
                action="error",
                user=user,
                args=args,
                error=str(exc),
            )

    print("", file=output)
    print(
        f"Apply summary: {applied} linked, {skipped} already linked, {errors} errors.",
        file=output,
    )
    if effective_audit_path:
        print(f"Audit: {effective_audit_path}", file=output)
    return 1 if errors else 0


def run_unlink(
    args: argparse.Namespace,
    *,
    audit_path: Optional[str] = None,
    output=sys.stdout,
) -> int:
    user = db.session.get(User, args.user_id)
    if user is None:
        print(f"user {args.user_id} not found", file=sys.stderr)
        return 1

    print(
        f"user_id={user.id} email={user.email} is_admin={user.is_admin} "
        f"current_link={user.supabase_auth_user_id}",
        file=output,
    )

    if user.supabase_auth_user_id is None:
        print("user is already unlinked, no-op", file=output)
        return 0

    if not args.apply:
        print("Dry-run: pass --apply to actually unlink.", file=output)
        return 0

    effective_audit_path = None
    if not args.no_audit:
        effective_audit_path = audit_path or args.audit_path or _generate_audit_path(args.audit_dir)

    previous = str(user.supabase_auth_user_id)
    try:
        unlink_app_user(user.id)
    except AppUserNotFound:
        print(f"user {args.user_id} not found", file=sys.stderr)
        return 1
    print(f"unlinked user_id={user.id} (was {previous})", file=output)
    _audit_record(
        effective_audit_path,
        action="unlink",
        user=user,
        args=args,
        supabase_uuid=previous,
    )
    if effective_audit_path:
        print(f"Audit: {effective_audit_path}", file=output)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_real_client(app) -> SupabaseAdminClient:
    url = app.config.get("SUPABASE_URL")
    key = app.config.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SupabaseAdminError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for --apply"
        )
    return SupabaseAdminClient(url, key)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    error = _validate_args(args)
    if error:
        print(error, file=sys.stderr)
        return 2

    app = create_app()
    with app.app_context():
        if args.unlink:
            return run_unlink(args)
        client: Optional[SupabaseAdminClient] = None
        if args.apply:
            try:
                client = _build_real_client(app)
            except SupabaseAdminError as exc:
                print(str(exc), file=sys.stderr)
                return 2
        return run_link(args, client=client)


if __name__ == "__main__":
    sys.exit(main())
