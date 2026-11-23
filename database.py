"""Database connection and schema for Attendly (Supabase / PostgreSQL).

create_tables() can run any number of times. It only adds what is missing,
so existing data is never deleted.

    python database.py      creates or updates the tables
"""
import logging
import re
import atexit
import os
import threading

from psycopg_pool import ConnectionPool
import psycopg

import config

log = logging.getLogger("attendly.db")

# Lets the rest of the app catch duplicate-key errors without knowing the driver.
IntegrityError = psycopg.IntegrityError

# Every table the app uses. Row Level Security is turned on for each one, so
# Supabase's public API (the anon key) cannot read or change them. The app
# connects as the table owner, which RLS does not block.
APP_TABLES = [
    "users",
    "students",
    "attendance",
    "classes",
    "subjects",
    "class_subjects",
    "student_enrollments",
    "attendance_sessions",
    "attendance_records",
    "login_attempts",
    "password_resets",
]

# The app's SQL uses SQLite-style placeholders (? and :name). psycopg uses
# %s and %(name)s, so every query is converted here before it runs.
_NAMED_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


class _Row(dict):
    """One result row. Works with row["name"] and with row[0], like the old sqlite3.Row."""

    def __init__(self, names, values):
        super().__init__()
        for name, value in zip(names, values):
            if name not in self:  # first column with a given name wins, like sqlite3.Row
                self[name] = value
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return dict.__getitem__(self, key)


def _row_factory(cursor):
    if cursor.description is None:
        return tuple
    names = [column.name for column in cursor.description]

    def make_row(values):
        return _Row(names, values)

    return make_row


def _translate(sql, params):
    """Convert SQLite-style placeholders into psycopg-style placeholders."""
    if params is None:
        return sql
    if isinstance(params, dict):
        return _NAMED_PARAM.sub(lambda m: "%(" + m.group(1) + ")s", sql)
    return sql.replace("?", "%s")

class _Connection:
    """Gives the rest of the app the same calls it used with SQLite."""

    def __init__(self, raw, pool):
        self._raw = raw
        self._pool = pool

    def execute(self, sql, params=None):
        return self._raw.execute(_translate(sql, params), params)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        if self._raw is not None:
            self._pool.putconn(self._raw)
            self._raw = None


_pool = None
_pool_pid = None
_pool_lock = threading.Lock()


def _get_pool():
    """Create one connection pool per application worker process."""
    global _pool, _pool_pid

    pid = os.getpid()

    if _pool is None or _pool_pid != pid:
        with _pool_lock:
            if _pool is None or _pool_pid != pid:
                _pool = ConnectionPool(
                    conninfo=config.DATABASE_URL,
                    min_size=1,
                    max_size=3,
                    timeout=10,
                    kwargs={
                        "row_factory": _row_factory,
                        "connect_timeout": 10,
                        "prepare_threshold": None,
                    },
                    open=True,
                    name=f"attendly-{pid}",
                )
                _pool_pid = pid

    return _pool


def _close_pool():
    """Close the worker's connection pool during process shutdown."""
    if _pool is not None and _pool_pid == os.getpid():
        _pool.close()


atexit.register(_close_pool)


def get_db_connection():
    """Get a reusable connection from the worker's pool."""
    if not config.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to the .env file (see .env.example)."
        )

    pool = _get_pool()
    raw = pool.getconn()

    return _Connection(raw, pool)


def ensure_columns(conn):
    """Add columns that newer versions need. Safe to run more than once."""
    exists = conn.execute(
        "SELECT to_regclass('attendance_records') IS NOT NULL AS ok"
    ).fetchone()["ok"]
    if not exists:
        return  # table not created yet; create_tables() makes it with these columns
    # 'student' = scanned the QR, 'teacher' = marked by hand. NULL = older rows.
    conn.execute("ALTER TABLE attendance_records ADD COLUMN IF NOT EXISTS marked_by TEXT")
    # IP address the QR scan came from. Kept as an audit trail for the network check.
    conn.execute("ALTER TABLE attendance_records ADD COLUMN IF NOT EXISTS marked_ip TEXT")


def create_tables():
    """Create and update database tables without deleting existing data."""
    conn = get_db_connection()

    try:
        # 1. USERS (both students and teachers)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL
                    CHECK (role IN ('teacher', 'student'))
            )
        """)

        # 2. STUDENTS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                user_id INTEGER UNIQUE NOT NULL,
                roll_number TEXT UNIQUE NOT NULL,
                course TEXT NOT NULL,

                FOREIGN KEY (user_id)
                    REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)

        # 3. STUDENT REGISTRATION FIELDS (added to older databases too)
        new_columns = {
            "dob": "TEXT",
            "gender": "TEXT",
            "phone": "TEXT",
            "alternate_phone": "TEXT",
            "department": "TEXT",
            "specialization": "TEXT",
            "year": "INTEGER",
            "semester": "INTEGER",
            "admission_year": "INTEGER",
            "section": "TEXT",
            "college": "TEXT",
            "address": "TEXT",
            "city": "TEXT",
            "state": "TEXT",
            "pin_code": "TEXT",
            "guardian_name": "TEXT",
            "guardian_phone": "TEXT",
            "residence_type": "TEXT",
        }
        for column_name, column_type in new_columns.items():
            conn.execute(
                f"ALTER TABLE students ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
            )

        # 4. ATTENDANCE (older table, kept so existing data is preserved)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                student_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,

                status TEXT NOT NULL
                    CHECK (status IN ('Present', 'Absent')),

                FOREIGN KEY (student_id)
                    REFERENCES students(id)
                    ON DELETE CASCADE,

                UNIQUE (student_id, attendance_date)
            )
        """)

        # 5. CLASSES (each teacher can create their own)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS classes (
                id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                class_name TEXT NOT NULL,
                section TEXT NOT NULL,
                created_by INTEGER NOT NULL,

                FOREIGN KEY (created_by)
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                UNIQUE (class_name, section, created_by)
            )
        """)

        # 6. SUBJECTS (each teacher can add their own)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subjects (
                id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                subject_name TEXT NOT NULL,
                subject_code TEXT NOT NULL,
                created_by INTEGER NOT NULL,

                FOREIGN KEY (created_by)
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                UNIQUE (subject_code, created_by)
            )
        """)

        # 7. CLASS <-> SUBJECT
        conn.execute("""
            CREATE TABLE IF NOT EXISTS class_subjects (
                id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                class_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,

                FOREIGN KEY (class_id)
                    REFERENCES classes(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (subject_id)
                    REFERENCES subjects(id)
                    ON DELETE CASCADE,

                UNIQUE (class_id, subject_id)
            )
        """)

        # 8. STUDENT ENROLLMENTS (which student is in which class)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS student_enrollments (
                id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                class_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,

                FOREIGN KEY (class_id)
                    REFERENCES classes(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (student_id)
                    REFERENCES students(id)
                    ON DELETE CASCADE,

                UNIQUE (class_id, student_id)
            )
        """)

        # 9. ATTENDANCE SESSIONS (one class period, with its own QR)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS attendance_sessions (
                id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                class_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                teacher_id INTEGER NOT NULL,
                session_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                qr_token TEXT UNIQUE,
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'closed')),

                FOREIGN KEY (class_id)
                    REFERENCES classes(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (subject_id)
                    REFERENCES subjects(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (teacher_id)
                    REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)

        # 10. ATTENDANCE RECORDS (one row per student per session)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS attendance_records (
                id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                session_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'Absent'
                    CHECK (status IN ('Present', 'Absent')),
                marked_at TEXT,
                marked_by TEXT,
                marked_ip TEXT,

                FOREIGN KEY (session_id)
                    REFERENCES attendance_sessions(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (student_id)
                    REFERENCES students(id)
                    ON DELETE CASCADE,

                UNIQUE (session_id, student_id)
            )
        """)

        # 11. LOGIN LOCKOUT (one row per email, see app.py)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                email TEXT PRIMARY KEY,
                failed_count INTEGER NOT NULL DEFAULT 0,
                last_failed_at TEXT,
                locked_until TEXT
            )
        """)

        # 12. PASSWORD RESET TOKENS (only a hash of each token is stored)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_resets (
                id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Columns added in later versions (older databases get them too).
        ensure_columns(conn)

        # Indexes for the lookups the app runs on most pages.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_student ON attendance_records(student_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_teacher ON attendance_sessions(teacher_id, class_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_enroll_student ON student_enrollments(student_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_resets_user ON password_resets(user_id)")

        # Block the public Supabase API from reading or writing app tables.
        for table in APP_TABLES:
            conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

        conn.commit()
        log.info("Database schema is up to date.")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    create_tables()
    print("Attendly database setup completed successfully!")
