"""Persistent user data layer shared by LangGraph and the frontend API.

The store keeps a public profile for personalization and keeps registration
secrets (password hashes, real name, and emergency contacts) server-side.
"""

from __future__ import annotations

import json
import os
import sqlite3
import secrets
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash


load_dotenv(Path(__file__).with_name(".env"), override=False)
DEFAULT_DB_PATH = Path(__file__).parent / "data" / "assistant_data.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_user_id(user_id: Any) -> str:
    value = str(user_id or "anonymous").strip()
    if not value:
        value = "anonymous"
    if len(value) > 128:
        raise ValueError("user_id 不能超过 128 个字符")
    return value


class DataStore:
    """SQLite repository for anonymous profiles, conversations, and TTS jobs."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.path = Path(db_path or os.getenv("DATA_DB_PATH", DEFAULT_DB_PATH))
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    nickname TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL DEFAULT '',
                    real_name TEXT NOT NULL DEFAULT '',
                    emergency_contacts_json TEXT NOT NULL DEFAULT '[]',
                    display_name TEXT NOT NULL DEFAULT '',
                    preferred_name TEXT NOT NULL DEFAULT '',
                    avatar_model TEXT NOT NULL DEFAULT '',
                    voice_profile TEXT NOT NULL DEFAULT '',
                    tts_enabled INTEGER NOT NULL DEFAULT 0,
                    profile_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
                CREATE TABLE IF NOT EXISTS assessments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    assessment_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT,
                    assessment_type TEXT NOT NULL,
                    risk_level TEXT NOT NULL DEFAULT 'unknown',
                    summary TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assessments_user ON assessments(user_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS tts_jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT,
                    text TEXT NOT NULL,
                    voice_profile TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT 'gpt-sovits',
                    status TEXT NOT NULL DEFAULT 'requested',
                    external_job_id TEXT NOT NULL DEFAULT '',
                    result_url TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE SET NULL
                );
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            for name, definition in {
                "nickname": "TEXT NOT NULL DEFAULT ''",
                "password_hash": "TEXT NOT NULL DEFAULT ''",
                "real_name": "TEXT NOT NULL DEFAULT ''",
                "emergency_contacts_json": "TEXT NOT NULL DEFAULT '[]'",
            }.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_nickname ON users(nickname) WHERE nickname <> ''")

    @staticmethod
    def _row(row: sqlite3.Row | None) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        value = dict(row)
        for key in ("profile_json", "metadata_json", "payload_json"):
            if key in value:
                try:
                    value[key] = json.loads(value[key])
                except (TypeError, json.JSONDecodeError):
                    value[key] = {}
        if "emergency_contacts_json" in value:
            try:
                value["emergency_contacts"] = json.loads(value.pop("emergency_contacts_json"))
            except (TypeError, json.JSONDecodeError):
                value["emergency_contacts"] = []
        value.pop("password_hash", None)
        return value

    def register_user(self, nickname: Any, password: Any, real_name: Any, emergency_contacts: Any) -> dict[str, Any]:
        nickname = str(nickname or "").strip()
        password = str(password or "")
        real_name = str(real_name or "").strip()
        contacts = emergency_contacts if isinstance(emergency_contacts, list) else []
        contacts = [
            dict(item)
            for item in contacts
            if isinstance(item, Mapping)
            and str(item.get("name", "")).strip()
            and str(item.get("phone", "")).strip()
            and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", str(item.get("email", "")).strip())
        ]
        if not 3 <= len(nickname) <= 40 or not all(ch.isalnum() or ch in "_-" for ch in nickname):
            raise ValueError("昵称需为 3-40 位字母、数字、下划线或短横线")
        if len(password) < 8:
            raise ValueError("密码至少需要 8 位")
        if not real_name or len(real_name) > 80:
            raise ValueError("请填写真实姓名")
        if not contacts:
            raise ValueError("至少需要填写一位含有效邮箱的紧急联系人")
        user_id = f"user-{uuid4().hex}"
        now = utc_now()
        try:
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO users(user_id,nickname,password_hash,real_name,emergency_contacts_json,display_name,created_at,updated_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (user_id, nickname, generate_password_hash(password), real_name, json.dumps(contacts, ensure_ascii=False), nickname, now, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("昵称已被注册") from exc
        return self.public_user(user_id)

    def authenticate_user(self, nickname: Any, password: Any) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE nickname=?", (str(nickname or "").strip(),)).fetchone()
        if not row or not row["password_hash"] or not check_password_hash(row["password_hash"], str(password or "")):
            return None
        return self.public_user(row["user_id"])

    def public_user(self, user_id: Any) -> dict[str, Any]:
        user = self.get_user(user_id) or {}
        user.pop("real_name", None)
        user.pop("emergency_contacts", None)
        return user

    def create_session(self, user_id: str, days: int = 30) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = generate_password_hash(token)
        now = utc_now()
        expires = datetime.now(timezone.utc).replace(microsecond=0)
        from datetime import timedelta
        expires = (expires + timedelta(days=days)).isoformat()
        with self.connect() as conn:
            conn.execute("INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES (?,?,?,?)", (token_hash, user_id, now, expires))
        return token

    def session_user(self, token: str) -> Optional[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT token_hash,user_id,expires_at FROM auth_sessions").fetchall()
        for row in rows:
            if check_password_hash(row["token_hash"], token):
                if row["expires_at"] < utc_now():
                    return None
                return row["user_id"]
        return None

    def revoke_session(self, token: str) -> None:
        with self.connect() as conn:
            rows = conn.execute("SELECT token_hash FROM auth_sessions").fetchall()
            for row in rows:
                if check_password_hash(row["token_hash"], token):
                    conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (row["token_hash"],))
                    break

    def ensure_user(self, user_id: Any) -> dict[str, Any]:
        user_id = validate_user_id(user_id)
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users(user_id,created_at,updated_at,last_seen_at) VALUES (?,?,?,?)",
                (user_id, now, now, now),
            )
            conn.execute("UPDATE users SET last_seen_at=?,updated_at=? WHERE user_id=?", (now, now, user_id))
            return self._row(conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()) or {}

    def get_user(self, user_id: Any) -> Optional[dict[str, Any]]:
        user_id = validate_user_id(user_id)
        with self.connect() as conn:
            return self._row(conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone())

    def update_user(self, user_id: Any, values: Mapping[str, Any]) -> dict[str, Any]:
        user_id = validate_user_id(user_id)
        current = self.ensure_user(user_id)
        allowed = {"display_name", "preferred_name", "avatar_model", "voice_profile", "tts_enabled"}
        updates = {key: values[key] for key in allowed if key in values}
        profile = current.get("profile_json") or {}
        if "preferences" in values and isinstance(values["preferences"], Mapping):
            profile = dict(values["preferences"])
            updates["profile_json"] = json.dumps(profile, ensure_ascii=False)
        if "tts_enabled" in updates:
            updates["tts_enabled"] = int(bool(updates["tts_enabled"]))
        for key in {"display_name", "preferred_name", "avatar_model", "voice_profile"} & updates.keys():
            updates[key] = str(updates[key]).strip()[:120]
        if updates:
            updates["updated_at"] = utc_now()
            columns = ", ".join(f"{key}=?" for key in updates)
            with self.connect() as conn:
                conn.execute(f"UPDATE users SET {columns} WHERE user_id=?", (*updates.values(), user_id))
        return self.get_user(user_id) or {}

    def user_context(self, user_id: Any) -> dict[str, Any]:
        user = self.ensure_user(user_id)
        return {
            "user_id": user["user_id"],
            "preferred_name": user.get("preferred_name") or user.get("display_name") or "",
            "avatar_model": user.get("avatar_model", ""),
            "voice_profile": user.get("voice_profile", ""),
            "tts_enabled": bool(user.get("tts_enabled")),
            "preferences": user.get("profile_json") or {},
        }

    def ensure_conversation(self, user_id: Any, conversation_id: Optional[str] = None, title: str = "") -> str:
        user_id = validate_user_id(user_id)
        self.ensure_user(user_id)
        conversation_id = str(conversation_id or f"conv-{uuid4().hex}").strip()
        if not conversation_id or len(conversation_id) > 128:
            raise ValueError("conversation_id 无效")
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO conversations(conversation_id,user_id,title,created_at,updated_at) VALUES (?,?,?,?,?)",
                (conversation_id, user_id, str(title).strip()[:120], now, now),
            )
            row = conn.execute("SELECT user_id FROM conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
            if row is None or row["user_id"] != user_id:
                raise ValueError("conversation_id 不属于当前用户")
            conn.execute("UPDATE conversations SET updated_at=? WHERE conversation_id=?", (now, conversation_id))
        return conversation_id

    def list_conversations(self, user_id: Any, limit: int = 50) -> list[dict[str, Any]]:
        user_id = validate_user_id(user_id)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM conversations WHERE user_id=? ORDER BY updated_at DESC LIMIT ?", (user_id, max(1, min(limit, 200)))
            ).fetchall()]

    def add_message(self, user_id: Any, conversation_id: str, role: str, content: Any, metadata: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("role 无效")
        user_id = validate_user_id(user_id)
        conversation_id = self.ensure_conversation(user_id, conversation_id)
        text = str(content or "").strip()
        if not text:
            raise ValueError("消息内容不能为空")
        if len(text) > 20000:
            raise ValueError("单条消息不能超过 20000 个字符")
        message_id, now = f"msg-{uuid4().hex}", utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO messages(message_id,conversation_id,user_id,role,content,metadata_json,created_at) VALUES (?,?,?,?,?,?,?)",
                (message_id, conversation_id, user_id, role, text, json.dumps(dict(metadata or {}), ensure_ascii=False), now),
            )
            conn.execute("UPDATE conversations SET updated_at=? WHERE conversation_id=?", (now, conversation_id))
            return self._row(conn.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()) or {}

    def list_messages(self, user_id: Any, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        user_id = validate_user_id(user_id)
        self.ensure_conversation(user_id, conversation_id)
        with self.connect() as conn:
            return [self._row(row) or {} for row in conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND user_id=? ORDER BY id DESC LIMIT ?",
                (conversation_id, user_id, max(1, min(limit, 500))),
            ).fetchall()][::-1]

    def save_assessment(self, user_id: Any, assessment_type: str, payload: Mapping[str, Any], conversation_id: Optional[str] = None) -> dict[str, Any]:
        user_id = validate_user_id(user_id)
        if conversation_id:
            conversation_id = self.ensure_conversation(user_id, conversation_id)
        assessment_id, now = f"assessment-{uuid4().hex}", utc_now()
        risk = str(payload.get("risk_level", "unknown")).lower()
        summary = str(payload.get("summary", payload.get("emotional_state", "")))[:1000]
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO assessments(assessment_id,user_id,conversation_id,assessment_type,risk_level,summary,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (assessment_id, user_id, conversation_id, assessment_type, risk, summary, json.dumps(dict(payload), ensure_ascii=False), now),
            )
            return self._row(conn.execute("SELECT * FROM assessments WHERE assessment_id=?", (assessment_id,)).fetchone()) or {}

    def list_assessments(self, user_id: Any, limit: int = 30) -> list[dict[str, Any]]:
        user_id = validate_user_id(user_id)
        with self.connect() as conn:
            return [self._row(row) or {} for row in conn.execute(
                "SELECT * FROM assessments WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, max(1, min(limit, 200)))
            ).fetchall()]

    def create_tts_job(self, user_id: Any, text: Any, conversation_id: Optional[str] = None, voice_profile: str = "") -> dict[str, Any]:
        user_id = validate_user_id(user_id)
        if conversation_id:
            conversation_id = self.ensure_conversation(user_id, conversation_id)
        text = str(text or "").strip()
        if not text:
            raise ValueError("text 不能为空")
        if len(text) > 5000:
            raise ValueError("语音文本不能超过 5000 个字符")
        job_id, now = f"tts-{uuid4().hex}", utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO tts_jobs(job_id,user_id,conversation_id,text,voice_profile,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (job_id, user_id, conversation_id, text, str(voice_profile).strip()[:120], now, now),
            )
            return dict(conn.execute("SELECT * FROM tts_jobs WHERE job_id=?", (job_id,)).fetchone())

    def get_tts_job(self, user_id: Any, job_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tts_jobs WHERE job_id=? AND user_id=?", (job_id, validate_user_id(user_id))).fetchone()
            return dict(row) if row else None

    def update_tts_job(self, user_id: Any, job_id: str, status: str, error: str = "") -> Optional[dict[str, Any]]:
        if status not in {"requested", "queued", "processing", "completed", "failed", "not_configured"}:
            raise ValueError("TTS 状态无效")
        with self.connect() as conn:
            conn.execute(
                "UPDATE tts_jobs SET status=?,error=?,updated_at=? WHERE job_id=? AND user_id=?",
                (status, str(error)[:1000], utc_now(), job_id, validate_user_id(user_id)),
            )
        return self.get_tts_job(user_id, job_id)

    def delete_user(self, user_id: Any) -> bool:
        with self.connect() as conn:
            return conn.execute("DELETE FROM users WHERE user_id=?", (validate_user_id(user_id),)).rowcount == 1
