"""Check that Attendly can reach its Supabase database.

Run:  python check_db.py
"""
import sys
from urllib.parse import urlsplit

import config
from database import APP_TABLES, get_db_connection


def show_target():
    """Print where we are connecting to. The password is never printed."""
    parts = urlsplit(config.DATABASE_URL)
    print(f"Host:     {parts.hostname}:{parts.port}")
    print(f"Database: {parts.path.lstrip('/')}")
    print(f"User:     {parts.username}")


def main():
    print("=== Attendly database check ===")

    if not config.DATABASE_URL:
        print("FAIL: DATABASE_URL is empty.")
        print("      Copy .env.example to .env and paste your connection string.")
        return 1

    show_target()

    try:
        conn = get_db_connection()
    except Exception as exc:
        print(f"FAIL: could not connect ({type(exc).__name__}): {exc}")
        print("      Check the password and the connection string (Connect > Session pooler).")
        print("      Check your internet connection too.")
        return 1

    try:
        info = conn.execute("SELECT version() AS v, current_user AS u").fetchone()
        print("OK:   connected to PostgreSQL")
        print(f"      Server:       {info['v'].split(',')[0]}")
        print(f"      Logged in as: {info['u']}")

        existing = {
            r["tablename"]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        }
        row_security = {
            r["tablename"]: r["rowsecurity"]
            for r in conn.execute(
                "SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        }

        print("\nTables:")
        missing = []
        for table in APP_TABLES:
            if table not in existing:
                missing.append(table)
                print(f"  MISSING   {table}")
                continue
            count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            rls = "on" if row_security.get(table) else "OFF"
            print(f"  ok        {table:<20} rows: {count:<6} row security: {rls}")

        if missing:
            print("\nFAIL: some tables are missing. Run:  python database.py")
            return 1

        off = [t for t in APP_TABLES if not row_security.get(t)]
        if off:
            print("\nWARNING: row security is OFF for some tables. Run:  python database.py")
            return 1

        print("\nAll checks passed. The app can use the database.")
        return 0

    except Exception as exc:
        print(f"FAIL: connected, but a query failed ({type(exc).__name__}): {exc}")
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
