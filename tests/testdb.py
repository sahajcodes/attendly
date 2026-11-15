"""Sends the tests to a SEPARATE test database. Every test file imports this first.

The tests erase every Attendly table in the test database, so it must never be
your real database. Set a second database (for example a second Supabase
project) in the environment before running the tests:

    ATTENDLY_TEST_DATABASE_URL=postgresql://...
"""
import os
import sys

PROJECT = os.environ.get("ATTENDLY_PROJECT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT)
os.chdir(PROJECT)

import config  # noqa: E402  (also loads .env, so the real database URL is known here)

TEST_URL = os.environ.get("ATTENDLY_TEST_DATABASE_URL", "").strip()
if not TEST_URL:
    sys.exit("Set ATTENDLY_TEST_DATABASE_URL to a separate TEST database first. The tests erase its tables.")
if TEST_URL == (config.DATABASE_URL or ""):
    sys.exit("REFUSED: ATTENDLY_TEST_DATABASE_URL is the same as DATABASE_URL (your real database). Use a different one.")

# Child processes that the tests start inherit this, so they use the test database too.
os.environ["DATABASE_URL"] = TEST_URL
config.DATABASE_URL = TEST_URL

import database  # noqa: E402


def reset_db():
    """Erase every Attendly table in the TEST database, then build them fresh."""
    conn = database.get_db_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS " + ", ".join(database.APP_TABLES) + " CASCADE")
        conn.commit()
    finally:
        conn.close()
    database.create_tables()
