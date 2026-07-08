"""
Structured audit log for Provenance Guard, backed by SQLite.
Every submission and every appeal writes a row here.
"""

import sqlite3
import json
from datetime import datetime, timezone

DB_PATH = "provenance_guard.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id TEXT NOT NULL,
            creator_id TEXT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,           -- "classified" or "appeal"
            attribution TEXT,                    -- "likely_ai" / "uncertain" / "likely_human"
            confidence REAL,
            llm_score REAL,
            stylometric_score REAL,
            status TEXT,                         -- "classified" or "under_review"
            creator_reasoning TEXT,               -- populated only for appeals
            raw_text_snippet TEXT                 -- first ~100 chars, for reviewer context
        )
    """)
    conn.commit()
    conn.close()


def log_classification(content_id, creator_id, attribution, confidence,
                        llm_score, stylometric_score, text):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO audit_log
        (content_id, creator_id, timestamp, event_type, attribution,
         confidence, llm_score, stylometric_score, status, raw_text_snippet)
        VALUES (?, ?, ?, 'classified', ?, ?, ?, ?, 'classified', ?)
    """, (
        content_id, creator_id, datetime.now(timezone.utc).isoformat(),
        attribution, confidence, llm_score, stylometric_score, text[:100]
    ))
    conn.commit()
    conn.close()


def log_appeal(content_id, creator_reasoning):
    """Logs the appeal and flips status to under_review on the original entry."""
    conn = sqlite3.connect(DB_PATH)

    # Insert a new row representing the appeal event
    conn.execute("""
        INSERT INTO audit_log
        (content_id, timestamp, event_type, status, creator_reasoning)
        VALUES (?, ?, 'appeal', 'under_review', ?)
    """, (content_id, datetime.now(timezone.utc).isoformat(), creator_reasoning))

    # Update the original classification row's status too
    conn.execute("""
        UPDATE audit_log SET status = 'under_review'
        WHERE content_id = ? AND event_type = 'classified'
    """, (content_id,))

    conn.commit()
    conn.close()


def get_recent_entries(limit=20):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM audit_log ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_original_entry(content_id):
    """Used by /appeal to confirm the content_id exists before logging an appeal."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT * FROM audit_log WHERE content_id = ? AND event_type = 'classified'
    """, (content_id,)).fetchone()
    conn.close()
    return dict(row) if row else None