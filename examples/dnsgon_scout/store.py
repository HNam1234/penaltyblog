"""Separate SQLite state for sports research; no finance or credentials.

Outbox delivery is at-most-once after an uncertain network outcome. Call
recover_sending() once at process startup, with only one publisher running.
"""

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import sqlite3


SCHEMA_VERSION = 1
TABLES = {"scout_snapshots", "scout_forecasts", "scout_results", "scout_runs", "scout_outbox", "scout_quota"}


def _text(value, limit=256):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("invalid_text")
    return value.strip()


def _json(value, limit=32768):
    try:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
        encoded = body.encode("utf-8")
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        raise ValueError("invalid_json") from None
    if len(encoded) > limit:
        raise ValueError("json_too_large")
    return body


def _time(value):
    try:
        instant = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError()
        return instant.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid_aware_timestamp") from None


def _day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("invalid_utc_day") from None
    return value


def _window(provider, day, window_days):
    provider, day = _text(provider), _day(day)
    if type(window_days) is not int or window_days < 1:
        raise ValueError("invalid_window_days")
    try:
        first = (date.fromisoformat(day) - timedelta(days=window_days - 1)).isoformat()
    except (ValueError, OverflowError):
        raise ValueError("invalid_window_days") from None
    return provider, day, first


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA busy_timeout=5000")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if not ((version == 0 and not tables) or (version == SCHEMA_VERSION and tables == TABLES)):
                raise ValueError("not_supported_scout_database")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("BEGIN IMMEDIATE")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if version == 0 and not tables:
                for sql in (
                    "CREATE TABLE scout_snapshots (id TEXT PRIMARY KEY, provider TEXT NOT NULL, fetched_at TEXT NOT NULL, body TEXT NOT NULL)",
                    "CREATE TABLE scout_forecasts (id TEXT PRIMARY KEY, body TEXT NOT NULL)",
                    "CREATE TABLE scout_results (id TEXT PRIMARY KEY, body TEXT NOT NULL)",
                    "CREATE TABLE scout_runs (id INTEGER PRIMARY KEY, status TEXT NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL)",
                    "CREATE TABLE scout_outbox (id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, text TEXT NOT NULL, expires_at TEXT, state TEXT NOT NULL CHECK(state IN ('pending','sending','sent','uncertain','failed')), message_id INTEGER, reason TEXT)",
                    "CREATE INDEX scout_outbox_pending ON scout_outbox(state,id)",
                    "CREATE TABLE scout_quota (provider TEXT NOT NULL, day TEXT NOT NULL, requests INTEGER NOT NULL CHECK(requests>=0), PRIMARY KEY(provider,day))",
                ):
                    self.db.execute(sql)
                self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION or tables != TABLES:
                raise ValueError("not_supported_scout_database")
            self.db.commit()
        except Exception:
            self.db.rollback()
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def snapshot(self, provider, data, fetched_at):
        provider, fetched_at, body = _text(provider), _time(fetched_at), _json(data, 2 * 1024 * 1024)
        identity = sha256((_json([provider, fetched_at]) + "\n" + body).encode()).hexdigest()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO scout_snapshots VALUES (?,?,?,?)", (identity, provider, fetched_at, body))
        return identity

    def forecast(self, candidate):
        if not isinstance(candidate, dict):
            raise ValueError("invalid_forecast")
        identity, body = _text(candidate.get("id")), _json(candidate)
        with self.db:
            return self.db.execute("INSERT OR IGNORE INTO scout_forecasts VALUES (?,?)", (identity, body)).rowcount == 1

    def predictions(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM scout_forecasts ORDER BY rowid")]

    def result(self, result):
        if not isinstance(result, dict):
            raise ValueError("invalid_result")
        # Keep contradictory outcomes separately: the scorer excludes ambiguity.
        contract = [_text(result.get(name)) for name in ("event_id", "sport", "market", "period")]
        line = result.get("line")
        if line is not None and (isinstance(line, bool) or not isinstance(line, (int, float))):
            raise ValueError("invalid_result_line")
        contract.extend([line, _text(result.get("outcome"))])
        payload = {**result, "settlement_verified": result.get("settlement_verified") is True}
        body = _json(payload)
        identity = sha256(_json(contract).encode()).hexdigest()
        with self.db:
            return self.db.execute("INSERT OR IGNORE INTO scout_results VALUES (?,?)", (identity, body)).rowcount == 1

    def results(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM scout_results ORDER BY rowid")]

    def record_run(self, status, detail, at):
        values = _text(status, 64), _json(detail), _time(at)
        with self.db:
            self.db.execute("INSERT INTO scout_runs(status,detail,at) VALUES (?,?,?)", values)

    def latest_run(self):
        row = self.db.execute("SELECT id,status,detail,at FROM scout_runs ORDER BY id DESC LIMIT 1").fetchone()
        return {**dict(row), "detail": json.loads(row["detail"])} if row else None

    def enqueue(self, key, text, expires_at=None):
        key, text = _text(key), _text(text, 16000)
        expires_at = _time(expires_at) if expires_at is not None else None
        with self.db:
            return self.db.execute("INSERT OR IGNORE INTO scout_outbox(key,text,expires_at,state) VALUES (?,?,?,'pending')", (key, text, expires_at)).rowcount == 1

    def next_message(self, now):
        row = self.db.execute("SELECT key,text,expires_at,state FROM scout_outbox WHERE state='pending' AND (expires_at IS NULL OR expires_at>?) ORDER BY id LIMIT 1", (_time(now),)).fetchone()
        return dict(row) if row else None

    def claim(self, key):
        with self.db:
            return self.db.execute("UPDATE scout_outbox SET state='sending' WHERE key=? AND state='pending'", (_text(key),)).rowcount == 1

    def mark_sent(self, key, message_id):
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id < 1:
            raise ValueError("invalid_message_id")
        with self.db:
            return self.db.execute("UPDATE scout_outbox SET state='sent',message_id=? WHERE key=? AND state='sending'", (message_id, _text(key))).rowcount == 1

    def mark_uncertain(self, key):
        return self._finish(key, "uncertain", "delivery_outcome_unknown")

    def mark_failed(self, key, reason):
        return self._finish(key, "failed", _text(reason, 1024))

    def _finish(self, key, state, reason):
        with self.db:
            return self.db.execute("UPDATE scout_outbox SET state=?,reason=? WHERE key=? AND state='sending'", (state, reason, _text(key))).rowcount == 1

    def recover_sending(self):
        """Call once on publisher startup; never resend an unconfirmed attempt."""
        with self.db:
            return self.db.execute("UPDATE scout_outbox SET state='uncertain',reason='interrupted_before_confirmation' WHERE state='sending'").rowcount

    def reserve_request(self, provider, day, limit=80, units=1):
        provider, day = _text(provider), _day(day)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("invalid_request_limit")
        if type(units) is not int or units < 1:
            raise ValueError("invalid_request_units")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO scout_quota VALUES (?,?,0)", (provider, day))
            return self.db.execute("UPDATE scout_quota SET requests=requests+? WHERE provider=? AND day=? AND requests<=?", (units, provider, day, limit-units)).rowcount == 1

    def request_count(self, provider, day):
        row = self.db.execute("SELECT requests FROM scout_quota WHERE provider=? AND day=?", (_text(provider), _day(day))).fetchone()
        return row[0] if row else 0

    def window_count(self, provider, day, window_days=31):
        """Credits reserved in this provider's inclusive UTC day window."""
        provider, day, first = _window(provider, day, window_days)
        return self.db.execute("SELECT COALESCE(SUM(requests),0) FROM scout_quota WHERE provider=? AND day>=? AND day<=?", (provider, first, day)).fetchone()[0]

    def reserve_window(self, provider, day, limit=450, units=1, window_days=31):
        """Atomically reserve before an attempt; calendar month changes do not reset it."""
        provider, day, first = _window(provider, day, window_days)
        if type(limit) is not int or not 0 <= limit <= 2**63 - 1:
            raise ValueError("invalid_request_limit")
        if type(units) is not int or not 1 <= units <= 2**63 - 1:
            raise ValueError("invalid_request_units")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            used = self.db.execute("SELECT COALESCE(SUM(requests),0) FROM scout_quota WHERE provider=? AND day>=? AND day<=?", (provider, first, day)).fetchone()[0]
            if units > limit - used:
                return False
            self.db.execute("INSERT INTO scout_quota VALUES (?,?,?) ON CONFLICT(provider,day) DO UPDATE SET requests=requests+excluded.requests", (provider, day, units))
            return True
