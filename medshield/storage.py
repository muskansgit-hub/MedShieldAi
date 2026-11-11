"""SQLite audit log: every guarded interaction is recorded for monitoring and review."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT,            -- analyze | ask | proxy
    provider TEXT,
    question TEXT,
    original_response TEXT,
    final_response TEXT,
    tier INTEGER,
    confidence REAL,
    incorrectness REAL,
    fsc REAL,
    fsc_after REAL,
    action TEXT,
    remediation TEXT,
    latency_ms REAL,
    report_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_ts ON interactions(ts);
"""


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def record(self, report: dict, source: str) -> int:
        after = report.get("after") or {}
        with self.lock:
            cur = self.conn.execute(
                """INSERT INTO interactions (ts, source, provider, question, original_response,
                   final_response, tier, confidence, incorrectness, fsc, fsc_after, action,
                   remediation, latency_ms, report_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (time.time(), source, report.get("provider"), report["question"],
                 report["original_response"], report["final_response"], report["risk"]["tier"],
                 report["confidence"]["score"], report["incorrectness"]["score"], report["fsc"],
                 after.get("fsc"), report["action"], report.get("remediation"),
                 report.get("timings_ms", {}).get("total"), json.dumps(report)),
            )
            self.conn.commit()
            return cur.lastrowid

    def recent(self, limit: int = 50, flagged_only: bool = False) -> list[dict]:
        q = ("SELECT id, ts, source, provider, question, tier, confidence, incorrectness, fsc, "
             "fsc_after, action, remediation, latency_ms FROM interactions")
        if flagged_only:
            q += " WHERE action != 'PASS'"
        q += " ORDER BY id DESC LIMIT ?"
        with self.lock:
            return [dict(r) for r in self.conn.execute(q, (limit,))]

    def get(self, rid: int) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT report_json FROM interactions WHERE id=?", (rid,)).fetchone()
        return json.loads(r["report_json"]) if r else None

    def stats(self) -> dict:
        with self.lock:
            c = self.conn
            total = c.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
            by_action = {r[0]: r[1] for r in c.execute("SELECT action, COUNT(*) FROM interactions GROUP BY action")}
            by_tier = [dict(r) for r in c.execute(
                "SELECT tier, COUNT(*) AS n, AVG(fsc) AS mean_fsc, AVG(COALESCE(fsc_after, fsc)) AS mean_fsc_after, "
                "SUM(action != 'PASS') AS flagged FROM interactions GROUP BY tier ORDER BY tier")]
            agg = c.execute("SELECT AVG(fsc), AVG(COALESCE(fsc_after, fsc)), AVG(latency_ms) FROM interactions").fetchone()
        return {
            "total": total,
            "by_action": {a: by_action.get(a, 0) for a in ("PASS", "WARN", "REWRITE", "BLOCK")},
            "by_tier": by_tier,
            "mean_fsc": agg[0], "mean_fsc_delivered": agg[1], "mean_latency_ms": agg[2],
        }

    def clear(self):
        with self.lock:
            self.conn.execute("DELETE FROM interactions")
            self.conn.commit()
