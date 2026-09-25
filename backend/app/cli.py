"""Admin CLI.

  python -m app.cli gen-key                      # print a new JA_ENCRYPTION_KEY
  python -m app.cli init-db                      # create tables + audit guards (dev/SQLite; use alembic for Postgres)
  python -m app.cli create-admin <username>      # prompts for password; prints the authenticator setup key + QR
  python -m app.cli reset-mfa <username> [--yes] # new authenticator secret (old one stops working), signs the user out
  python -m app.cli pause "<reason>" | resume
  python -m app.cli poll                         # one poll + match + prepare cycle (respects pause)
  python -m app.cli verify-audit
"""
import getpass
import sys

from sqlalchemy import select


def print_totp_setup(uri: str, out=None) -> None:  # noqa: ANN001
    """Setup key (for typing into the app) plus a terminal QR code (for scanning). Shown once, never stored."""
    import sys as _sys
    from urllib.parse import parse_qs, unquote, urlparse

    import segno

    out = out or _sys.stdout
    u = urlparse(uri)
    q = parse_qs(u.query)
    secret = q.get("secret", [""])[0]
    account = unquote(u.path.lstrip("/"))
    grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
    print("\nAdd this to your authenticator app (shown ONCE; it is not stored anywhere readable):", file=out)
    print("  Option 1 - scan this QR code with the app (+ -> Scan a QR code):\n", file=out)
    segno.make(uri, error="m").terminal(out=out, compact=True, border=2)
    print("\n  Option 2 - enter it by hand (+ -> Enter a setup key):", file=out)
    print(f"    Account:    {account}", file=out)
    print(f"    Setup key:  {grouped}", file=out)
    print("    Type:       Time based (TOTP), 6 digits, 30 seconds", file=out)
    print(f"\n  Option 3 - password managers: paste this link:\n    {uri}", file=out)
    print("\nThen clear this terminal (Cmd+K) so the key is not left on screen.\n", file=out)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "gen-key":
        from .security.crypto import generate_key

        print(generate_key())
        return 0

    from . import audit, controls, discovery, pipeline
    from .connectors.registry import sync_registry
    from .db import Base, SessionLocal, get_engine
    from .models import Profile

    if cmd == "init-db":
        eng = get_engine()
        Base.metadata.create_all(eng)
        with eng.begin() as conn:
            audit.install_audit_guards(conn)
        db = SessionLocal()
        sync_registry(db)
        db.close()
        print("database initialised")
        return 0
    db = SessionLocal()
    try:
        if cmd == "create-admin":
            from .security.auth import create_user

            pw = getpass.getpass("Password (min 12 chars): ")
            if pw != getpass.getpass("Repeat: "):
                print("passwords differ")
                return 1
            _, uri = create_user(db, argv[1], pw, "admin", with_totp=True)
            print("Admin created.")
            print_totp_setup(uri)
            return 0
        if cmd == "reset-mfa":
            from .security.auth import reset_totp

            if len(argv) < 2:
                print("usage: reset-mfa <username> [--yes]")
                return 1
            if "--yes" not in argv:
                print(f"This replaces the authenticator secret for {argv[1]!r}. The old authenticator entry stops working")
                print("and all of this user's sessions are signed out.")
                if input(f"Type {argv[1]} to confirm: ").strip() != argv[1]:
                    print("cancelled")
                    return 1
            try:
                _, uri, _ = reset_totp(db, argv[1], "cli")
            except ValueError as e:
                print(e)
                return 1
            print("Authenticator secret replaced.")
            print_totp_setup(uri)
            return 0
        if cmd == "pause":
            print(controls.set_paused(db, True, "cli", " ".join(argv[1:]) or "cli pause"))
            return 0
        if cmd == "resume":
            print(controls.set_paused(db, False, "cli", "cli resume"))
            return 0
        if cmd == "poll":
            runs = discovery.poll_all(db)
            for r in runs:
                print(f"{r.source_key}/{r.board_token}: {r.status} seen={r.jobs_seen} new={r.jobs_new} "
                      f"expired={r.jobs_expired} {r.error or ''}")
            p = db.execute(select(Profile).limit(1)).scalar()
            if p:
                print(pipeline.auto_process(db, p))
            return 0
        if cmd == "verify-audit":
            print(audit.verify_chain(db))
            return 0
    finally:
        db.close()
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
