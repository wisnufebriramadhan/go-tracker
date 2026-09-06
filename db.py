"""SQLite storage for geo-tracker sessions and location fixes.

Uses only the Python standard library (sqlite3) — no extra dependencies.
Enhanced with connection pooling and database indexes for better performance.
"""

import sqlite3
from datetime import datetime, timezone
import os
import secrets
from contextlib import contextmanager
from exceptions import DatabaseError as BaseDatabaseError, SessionNotFoundError, InvalidLocationError

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "geo.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    paused INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    accuracy REAL,
    source TEXT NOT NULL DEFAULT 'gps',
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_locations_session_id ON locations(session_id);
CREATE INDEX IF NOT EXISTS idx_locations_timestamp ON locations(timestamp);
CREATE INDEX IF NOT EXISTS idx_locations_session_timestamp ON locations(session_id, timestamp);
"""


class DatabaseError(BaseDatabaseError):
    """Base exception for database operations."""
    pass

# Re-export from exceptions for backward compatibility
__all__ = ['DatabaseError', 'SessionNotFoundError', 'InvalidLocationError']


# Connection pool (simple implementation)
_connection_pool = []
_pool_lock = None


def _init_pool():
    """Initialize the connection pool lock (lazy)."""
    global _pool_lock
    if _pool_lock is None:
        import threading
        _pool_lock = threading.Lock()


@contextmanager
def get_connection():
    """Context manager for database connections with pooling."""
    _init_pool()
    conn = None
    try:
        with _pool_lock:
            if _connection_pool:
                conn = _connection_pool.pop()
                try:
                    conn.execute("SELECT 1")
                except sqlite3.Error:
                    conn.close()
                    conn = None

        if conn is None:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")

        yield conn
    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        raise DatabaseError(f"Database error: {e}")
    finally:
        if conn:
            with _pool_lock:
                if len(_connection_pool) < 10:  # Max pool size
                    _connection_pool.append(conn)
                else:
                    conn.close()


def get_conn():
    """Return a new SQLite connection with row access by column name."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(drop_existing=False):
    """Create tables if they don't exist yet.
    
    Args:
        drop_existing: If True, drop all tables first (useful for testing)
    """
    with get_connection() as conn:
        if drop_existing:
            conn.execute("DROP TABLE IF EXISTS locations")
            conn.execute("DROP TABLE IF EXISTS sessions")
            conn.commit()
        conn.executescript(SCHEMA)
        conn.commit()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def create_session(name=None, ttl_hours=None):
    """Create a new sharing session and return it as a dict."""
    token = secrets.token_urlsafe(16)
    if not name or not name.strip():
        name = "Session"

    # Calculate expiration if TTL is set
    expires_at = None
    if ttl_hours and ttl_hours > 0:
        from datetime import timedelta
        expires_time = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        expires_at = expires_time.isoformat()

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (token, name, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, name.strip(), _now_iso(), expires_at),
        )
        conn.commit()
        return get_session_by_id(cur.lastrowid)


def get_session_by_token(token, include_deleted=False):
    """Return a session dict for a token, or None."""
    with get_connection() as conn:
        query = "SELECT * FROM sessions WHERE token = ?"
        if not include_deleted:
            query += " AND is_deleted = 0"
        row = conn.execute(query, (token,)).fetchone()
        return dict(row) if row else None


def get_session_by_id(session_id, include_deleted=False):
    """Return a session dict for an id, or None."""
    with get_connection() as conn:
        query = "SELECT * FROM sessions WHERE id = ?"
        if not include_deleted:
            query += " AND is_deleted = 0"
        row = conn.execute(query, (session_id,)).fetchone()
        return dict(row) if row else None


def list_sessions(include_deleted=False):
    """Return all sessions, newest first."""
    with get_connection() as conn:
        query = "SELECT * FROM sessions"
        if not include_deleted:
            query += " WHERE is_deleted = 0"
        query += " ORDER BY id DESC"
        rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]


def update_session(session_id, **kwargs):
    """Update session fields."""
    allowed_fields = {"name", "paused", "expires_at"}
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    if not updates:
        return

    updates["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [session_id]

    with get_connection() as conn:
        conn.execute(f"UPDATE sessions SET {set_clause} WHERE id = ?", values)
        conn.commit()


def soft_delete_session(session_id):
    """Soft delete a session (mark as deleted)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE sessions SET is_deleted = 1, updated_at = ? WHERE id = ?",
            (_now_iso(), session_id),
        )
        conn.commit()


def delete_session(session_id):
    """Hard delete a session and all of its locations."""
    with get_connection() as conn:
        conn.execute("DELETE FROM locations WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()


def save_location(token, lat, lon, accuracy=None, source="gps"):
    """Record a GPS fix for a session token. Returns the saved row, or None if token unknown."""
    session = get_session_by_token(token)
    if session is None:
        return None

    # Check if session is paused
    if session.get("paused"):
        raise InvalidLocationError("Session is paused")

    # Check if session is expired
    if session.get("expires_at"):
        from datetime import datetime as dt
        expires = dt.fromisoformat(session["expires_at"])
        if dt.now(timezone.utc) > expires:
            raise InvalidLocationError("Session has expired")

    # Validate coordinates
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise InvalidLocationError("Invalid coordinates")

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO locations (session_id, lat, lon, accuracy, source, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session["id"], lat, lon, accuracy, source, _now_iso()),
        )
        conn.commit()

        # Update session's updated_at
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (_now_iso(), session["id"]),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM locations WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def get_trail(token):
    """Return the ordered list of location fixes for a session token."""
    session = get_session_by_token(token)
    if session is None:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM locations WHERE session_id = ? ORDER BY id ASC",
            (session["id"],),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_location(token):
    """Return the most recent fix for a session token, or None."""
    trail = get_trail(token)
    return trail[-1] if trail else None


def get_sessions_with_status(stale_after_seconds=120):
    """Return all sessions plus their latest fix and trail length.

    Each item: session dict + 'latest' (fix dict or None) + 'points' count
    + 'active' bool (latest fix within stale_after_seconds).
    """
    sessions = list_sessions()
    result = []
    for s in sessions:
        # Skip expired sessions
        if s.get("expires_at"):
            try:
                expires = datetime.fromisoformat(s["expires_at"])
                if datetime.now(timezone.utc) > expires:
                    continue
            except ValueError:
                pass

        trail = get_trail(s["token"])
        latest = trail[-1] if trail else None
        active = False
        if latest:
            try:
                ts = datetime.fromisoformat(latest["timestamp"])
                age = (datetime.now(timezone.utc) - ts).total_seconds()
                active = age <= stale_after_seconds
            except ValueError:
                active = False
        result.append(
            {
                "id": s["id"],
                "token": s["token"],
                "name": s["name"],
                "created_at": s["created_at"],
                "paused": bool(s.get("paused")),
                "points": len(trail),
                "active": active,
                "latest": latest,
            }
        )
    return result


def delete_expired_sessions():
    """Delete sessions that have passed their expiration time."""
    with get_connection() as conn:
        now = _now_iso()
        conn.execute(
            "DELETE FROM locations WHERE session_id IN "
            "(SELECT id FROM sessions WHERE expires_at IS NOT NULL AND expires_at < ?)",
            (now,),
        )
        conn.execute(
            "DELETE FROM sessions WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )
        conn.commit()


def get_session_stats():
    """Get summary statistics about sessions."""
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM sessions WHERE is_deleted = 0").fetchone()[0]
        active_sessions = 0
        total_points = 0

        sessions = list_sessions()
        for s in sessions:
            trail = get_trail(s["token"])
            if trail:
                total_points += len(trail)
                latest = trail[-1]
                try:
                    ts = datetime.fromisoformat(latest["timestamp"])
                    age = (datetime.now(timezone.utc) - ts).total_seconds()
                    if age <= 120:
                        active_sessions += 1
                except ValueError:
                    pass

        return {
            "total_sessions": total,
            "active_sessions": active_sessions,
            "total_points": total_points,
        }
