"""Admin CLI.

  python -m app.cli gen-key                      # print a new JA_ENCRYPTION_KEY
  python -m app.cli init-db                      # create tables + audit guards (dev/SQLite; use alembic for Postgres)
  python -m app.cli create-admin <username>      # prompts for password; prints TOTP provisioning URI
  python -m app.cli pause "<reason>" | resume
  python -m app.cli poll                         # one poll + match + prepare cycle (respects pause)
  python -m app.cli verify-audit
"""
import getpass
import sys

from sqlalchemy import select


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
            print("Admin created. Add this to your authenticator app (shown once):")
            print(uri)
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
