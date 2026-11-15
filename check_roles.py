"""Quick check: how many users each role has, and which tables exist."""
from database import get_db_connection

conn = get_db_connection()
try:
    print("=== Users by role ===")
    for row in conn.execute(
        "SELECT role, COUNT(*) AS total FROM users GROUP BY role ORDER BY role"
    ).fetchall():
        print(f"{row['role']}: {row['total']}")

    print("\n=== Tables in the public schema ===")
    for row in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    ).fetchall():
        print(row["tablename"])
finally:
    conn.close()
