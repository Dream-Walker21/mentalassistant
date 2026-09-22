"""HTTP alert receiver and pluggable alert delivery service.

Run this file as a small independent service. LangGraph posts to ``/alert``;
this module decides whether the payload is delivered by email, a webhook, or
just logged. Set ``ALERT_CHANNEL`` to switch channels without changing the
conversation workflow.
"""

from __future__ import annotations

import html
import json
import os
import secrets
import smtplib
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from uuid import uuid4

import psycopg
import structlog
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from werkzeug.security import check_password_hash, generate_password_hash

from ..common.data_layer import DataStore
from ..common.logging_config import setup_logging

# Load deployment settings from the project-local file when this module is
# started directly. Existing process environment variables remain authoritative.
ENV_FILE = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=ENV_FILE, override=False)

setup_logging()
logger = structlog.get_logger("xinqing.alert")
app = Flask(__name__)
app.secret_key = os.getenv("ADMIN_SESSION_SECRET", "local-development-session-secret")
DB_URL = os.getenv("ALERT_DB_URL", "postgresql://xinqing:xinqing@localhost:5432/xinqing")
API_TOKEN = os.getenv("ALERT_API_TOKEN", "").strip()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
DEFAULT_ADMIN_USERNAME = os.getenv("ADMIN_DEFAULT_USERNAME", "admin").strip() or "admin"
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_DEFAULT_PASSWORD", "change-me-now").strip()
_data_store: DataStore | None = None
ROLE_PERMISSIONS = {
    "admin": {"view_alerts", "manage_alerts", "manage_users"},
    "operator": {"view_alerts", "manage_alerts"},
    "viewer": {"view_alerts"},
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _db() -> psycopg.Connection:
    conn = psycopg.connect(DB_URL, autocommit=False)
    conn.row_factory = dict_row
    return conn


def init_db() -> None:
    with _db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
          alert_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, received_at TEXT NOT NULL,
          source_timestamp TEXT, risk_level TEXT NOT NULL, urgency TEXT, stress_level TEXT,
          immediate_action INTEGER NOT NULL DEFAULT 0, handling_status TEXT NOT NULL DEFAULT 'new',
          email_status TEXT NOT NULL DEFAULT 'pending', email_error TEXT, handled_by TEXT,
          handling_note TEXT, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS email_deliveries (
          id SERIAL PRIMARY KEY, alert_id TEXT NOT NULL, attempted_at TEXT NOT NULL,
          status TEXT NOT NULL, recipients_count INTEGER NOT NULL DEFAULT 0, error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_alerts_received ON alerts(received_at DESC);
        CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id);
        CREATE TABLE IF NOT EXISTS admin_users (
          id SERIAL PRIMARY KEY,
          username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'viewer',
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """)
        row = conn.execute(
            "SELECT id FROM admin_users WHERE username=%s", (DEFAULT_ADMIN_USERNAME,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO admin_users(username,password_hash,role,enabled,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    DEFAULT_ADMIN_USERNAME,
                    generate_password_hash(DEFAULT_ADMIN_PASSWORD),
                    "admin",
                    1,
                    _now(),
                    _now(),
                ),
            )


def _save_alert(payload: Mapping[str, Any]) -> bool:
    risk = payload.get("risk_assessment") or {}
    received = _now()
    with _db() as conn:
        cur = conn.execute(
            """
          INSERT INTO alerts
          (alert_id,user_id,received_at,source_timestamp,risk_level,urgency,stress_level,immediate_action,payload_json,updated_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT (alert_id) DO NOTHING
        """,
            (
                str(payload["alert_id"]),
                str(payload["user_id"]),
                received,
                str(payload.get("timestamp", "")),
                str(risk.get("risk_level", "unknown")).lower(),
                str(risk.get("urgency", "未知")),
                str(risk.get("stress_level", "未知")),
                int(bool(risk.get("immediate_action_required") or risk.get("risk_flag"))),
                json.dumps(dict(payload), ensure_ascii=False),
                received,
            ),
        )
        return cur.rowcount == 1


def _record_email(
    alert_id: str, status: str, error: str | None = None, recipients: int = 0
) -> None:
    timestamp = _now()
    with _db() as conn:
        conn.execute(
            "UPDATE alerts SET email_status=%s,email_error=%s,updated_at=%s WHERE alert_id=%s",
            (status, error, timestamp, alert_id),
        )
        conn.execute(
            "INSERT INTO email_deliveries(alert_id,attempted_at,status,recipients_count,error) VALUES (%s,%s,%s,%s,%s)",
            (alert_id, timestamp, status, recipients, error),
        )


def _get_alert(alert_id: str):
    with _db() as conn:
        return conn.execute("SELECT * FROM alerts WHERE alert_id=%s", (alert_id,)).fetchone()


def _authorized() -> bool:
    if not API_TOKEN:
        return True
    value = request.headers.get("X-API-Key", "")
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return secrets.compare_digest(value, API_TOKEN)


def current_admin() -> dict[str, Any] | None:
    user_id = session.get("admin_user_id")
    if not user_id:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM admin_users WHERE id=%s AND enabled=1", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def require_permission(permission: str = "view_alerts"):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_admin()
            if user and permission in ROLE_PERMISSIONS.get(user["role"], set()):
                return view(*args, **kwargs)
            # Backwards-compatible token access remains administrator-level.
            value = request.headers.get("X-Admin-Token", "")
            if ADMIN_TOKEN and value and secrets.compare_digest(value, ADMIN_TOKEN):
                return view(*args, **kwargs)
            return redirect(url_for("admin_login", next=request.path))

        return wrapped

    return decorator


def has_permission(permission: str) -> bool:
    admin = current_admin()
    if admin and permission in ROLE_PERMISSIONS.get(admin.get("role"), set()):
        return True
    value = request.headers.get("X-Admin-Token", "")
    return bool(ADMIN_TOKEN and value and secrets.compare_digest(value, ADMIN_TOKEN))


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_admin() or session.get("admin_authenticated"):
            return view(*args, **kwargs)
        value = request.headers.get("X-Admin-Token", "")
        if ADMIN_TOKEN and value and secrets.compare_digest(value, ADMIN_TOKEN):
            return view(*args, **kwargs)
        return redirect(url_for("admin_login", next=request.path))

    return wrapped


def _env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def _is_high_risk(payload: Mapping[str, Any]) -> bool:
    risk = payload.get("risk_assessment") or {}
    if not isinstance(risk, Mapping):
        return False
    level = str(risk.get("risk_level", "")).strip().lower()
    urgency = str(risk.get("urgency", "")).strip().lower()
    return bool(
        level in {"high", "critical", "严重", "高危", "危急"}
        or risk.get("risk_flag")
        or urgency in {"high", "critical", "高", "紧急", "危急"}
    )


def _lookup_sensitive_user(user_id: str) -> dict[str, Any] | None:
    """Read private registration details from the shared user database."""
    global _data_store
    try:
        if _data_store is None:
            _data_store = DataStore()
        user = _data_store.get_user(user_id)
        if not user:
            return None
        contacts = user.get("emergency_contacts") or []
        return {
            "real_name": str(user.get("real_name") or "").strip(),
            "emergency_contacts": [
                {
                    "name": str(item.get("name") or "").strip(),
                    "phone": str(item.get("phone") or "").strip(),
                    "email": str(item.get("email") or "").strip(),
                }
                for item in contacts
                if isinstance(item, Mapping)
                and str(item.get("name") or "").strip()
                and str(item.get("phone") or "").strip()
                and str(item.get("email") or "").strip()
            ],
        }
    except Exception:
        logger.exception("lookup_sensitive_user_failed", user_id=user_id)
        return None


def _attach_sensitive_user(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Attach contact details only for high-risk alerts."""
    enriched = dict(payload)
    # Never trust sensitive fields supplied by the graph or a caller.
    enriched.pop("real_name", None)
    enriched.pop("emergency_contacts", None)
    if _is_high_risk(enriched):
        private = _lookup_sensitive_user(str(enriched.get("user_id", "anonymous")))
        if private and (private["real_name"] or private["emergency_contacts"]):
            enriched.update(private)
    return enriched


def _normalize_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    """Accept both the LangGraph wrapper and a raw alert object."""

    value = data.get("alert_data", data)
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise ValueError("alert_data 必须是对象或 JSON 字符串")
    payload = dict(value)
    payload.setdefault("alert_id", f"alert-{uuid4().hex[:12]}")
    raw_user_id = payload.get("user_id", payload.get("userId"))
    payload["user_id"] = str(raw_user_id).strip() if raw_user_id is not None else "anonymous"
    payload["user_id"] = payload["user_id"] or "anonymous"
    payload.setdefault("risk_assessment", {})
    return _attach_sensitive_user(payload)


class AlertNotifier(ABC):
    """Delivery interface. Add another implementation without touching graph.py."""

    @abstractmethod
    def send(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class LogNotifier(AlertNotifier):
    def send(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        risk = payload.get("risk_assessment") or {}
        logger.warning(
            "crisis_alert_received",
            alert_id=payload.get("alert_id"),
            user_id=payload.get("user_id"),
            risk_level=risk.get("risk_level", "unknown"),
            urgency=risk.get("urgency", "unknown"),
        )
        return {"channel": "log"}


class EmailNotifier(AlertNotifier):
    def __init__(self) -> None:
        self.server = os.getenv("SMTP_SERVER", "smtp.qq.com")
        self.port = int(os.getenv("SMTP_PORT", "465"))
        self.username = os.getenv("SMTP_USERNAME", os.getenv("EMAIL_ADDRESS", ""))
        self.password = os.getenv("SMTP_PASSWORD", os.getenv("EMAIL_PASSWORD", ""))
        self.from_email = os.getenv("SMTP_FROM", self.username)
        self.recipients = _env_list("ALERT_RECIPIENTS")

    def send(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not self.username or not self.password:
            raise RuntimeError("邮件通道缺少 SMTP_USERNAME/SMTP_PASSWORD 配置")

        risk = payload.get("risk_assessment") or {}
        risk_level = str(risk.get("risk_level", "unknown")).lower()
        subject = {
            "critical": "紧急心理危机预警",
            "high": "高度心理风险预警",
            "medium": "中度心理风险提醒",
            "low": "心理状态关注提醒",
        }.get(risk_level, "心理状态评估通知")
        contact_recipients = [
            str(item.get("email")).strip()
            for item in (payload.get("emergency_contacts") or [])
            if isinstance(item, Mapping) and str(item.get("email") or "").strip()
        ]
        recipients = list(dict.fromkeys([*self.recipients, *contact_recipients]))
        if not recipients:
            raise RuntimeError(
                "邮件通道缺少 SMTP_USERNAME/SMTP_PASSWORD/ALERT_RECIPIENTS，且没有用户紧急联系人邮箱"
            )
        query = html.escape(str(payload.get("query", ""))[:500])
        signals = risk.get("risk_signals") or []
        signal_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in signals)
        private_html = ""
        if payload.get("real_name") or payload.get("emergency_contacts"):
            contacts = payload.get("emergency_contacts") or []
            contact_lines = "<br>".join(
                f"{html.escape(str(item.get('name', '')))}：{html.escape(str(item.get('phone', '')))}（{html.escape(str(item.get('email', '')))}）"
                for item in contacts
                if isinstance(item, Mapping)
            )
            private_html = (
                f"<p><b>真实姓名：</b>{html.escape(str(payload.get('real_name', '')))}</p>"
                f"<p><b>紧急联系人：</b>{contact_lines or '未提供'}</p>"
            )
        body = f"""
        <html><body>
        <h2>{html.escape(subject)}</h2>
        <p><b>告警 ID：</b>{html.escape(str(payload.get("alert_id", "unknown")))}</p>
        <p><b>用户 ID：</b>{html.escape(str(payload.get("user_id", "anonymous")))}</p>
        <p><b>时间：</b>{html.escape(str(payload.get("timestamp", "unknown")))}</p>
        <p><b>风险等级：</b>{html.escape(risk_level)}</p>
        <p><b>紧急程度：</b>{html.escape(str(risk.get("urgency", "unknown")))}</p>
        <p><b>压力等级：</b>{html.escape(str(risk.get("stress_level", "unknown")))} / 5</p>
        <p><b>风险信号：</b></p><ul>{signal_html or "<li>未提供</li>"}</ul>
        <p><b>输入摘要：</b></p>
        <blockquote>{query or "未提供"}</blockquote>
        {private_html}
        <p>此邮件由心晴助手告警服务自动生成，请由具备资质的工作人员进行后续判断。</p>
        </body></html>
        """
        message = MIMEMultipart()
        message["From"] = self.from_email
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message.attach(MIMEText(body, "html", "utf-8"))
        with smtplib.SMTP_SSL(self.server, self.port, timeout=30) as server:
            server.login(self.username, self.password)
            server.send_message(message)
        return {"channel": "email", "recipients_count": len(recipients)}


class WebhookNotifier(AlertNotifier):
    def __init__(self, url: str) -> None:
        self.url = url

    def send(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        req = Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Webhook 返回 HTTP {response.status}")
        return {"channel": "webhook"}


def create_notifier() -> AlertNotifier:
    channel = os.getenv("ALERT_CHANNEL", "email").lower()
    if channel == "log":
        return LogNotifier()
    if channel == "webhook":
        url = os.getenv("ALERT_WEBHOOK_URL", "")
        if not url:
            raise RuntimeError("ALERT_CHANNEL=webhook 时必须配置 ALERT_WEBHOOK_URL")
        return WebhookNotifier(url)
    if channel == "email":
        return EmailNotifier()
    raise RuntimeError(f"不支持的 ALERT_CHANNEL: {channel}")


notifier = create_notifier()


@app.get("/health")
def health() -> Any:
    return jsonify(
        {
            "status": "healthy",
            "service": "xin-qing-alert",
            "channel": os.getenv("ALERT_CHANNEL", "email"),
        }
    )


@app.post("/alert")
def receive_alert() -> Any:
    if not _authorized():
        return jsonify({"status": "error", "error": "未授权"}), 401
    try:
        data = request.get_json(silent=False)
        if not isinstance(data, Mapping):
            return jsonify({"status": "error", "error": "请求体必须是 JSON 对象"}), 400
        payload = _normalize_payload(data)
        init_db()
        if not _save_alert(payload):
            row = _get_alert(str(payload["alert_id"]))
            return jsonify(
                {
                    "status": "success",
                    "alert_id": payload["alert_id"],
                    "duplicate": True,
                    "email_status": row["email_status"] if row else "unknown",
                }
            )
        alert_id = str(payload["alert_id"])
        _record_email(alert_id, "sending")
        try:
            result = notifier.send(payload)
            _record_email(alert_id, "sent", recipients=int(result.get("recipients_count", 0)))
        except Exception as exc:
            logger.exception("alert_send_failed", alert_id=alert_id)
            _record_email(alert_id, "failed", error=str(exc))
            return jsonify(
                {
                    "status": "error",
                    "alert_id": alert_id,
                    "error": "邮件发送失败",
                    "detail": str(exc),
                }
            ), 500
        return jsonify({"status": "success", "alert_id": alert_id, **result})
    except json.JSONDecodeError as exc:
        return jsonify({"status": "error", "error": f"alert_data 不是有效 JSON: {exc}"}), 400
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("alert_handler_error")
        return jsonify({"status": "error", "error": str(exc)}), 500


BASE_STYLE = """<style>:root{--ink:#24343b;--muted:#718087;--accent:#0f766e;--soft:#e7f5f1;--line:#dce7e5}*{box-sizing:border-box}body{margin:0;font-family:system-ui,'Microsoft YaHei',sans-serif;background:linear-gradient(135deg,#eaf7f4,#f7faf9);color:var(--ink)}.top{background:#123f46;color:#fff;padding:18px 28px;display:flex;justify-content:space-between;align-items:center}.top a{color:#d5eeea;text-decoration:none;margin-left:16px}.wrap{max-width:1280px;margin:26px auto;padding:0 18px}.panel,.filters,.table{background:rgba(255,255,255,.9);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:16px;box-shadow:0 12px 30px rgba(27,65,70,.07)}.filters{display:flex;gap:10px;flex-wrap:wrap}.filters input,.filters select{flex:1;min-width:150px}input,select,textarea,button{border:1px solid var(--line);border-radius:7px;padding:10px;font:inherit}button{background:var(--accent);color:white;border:0;cursor:pointer}button.secondary{background:var(--soft);color:var(--accent)}table{width:100%;border-collapse:collapse}th,td{padding:13px 10px;border-bottom:1px solid #edf2f1;text-align:left}.critical{color:#b42318;font-weight:700}.high{color:#c2410c;font-weight:700}.medium{color:#a16207}.badge{padding:4px 8px;border-radius:99px;background:var(--soft);font-size:12px}.actions a{color:var(--accent);text-decoration:none}.muted{color:var(--muted);font-size:13px}.auth{max-width:430px;margin:12vh auto}.auth h1{margin-top:0}.auth form{display:grid;gap:10px}.nav{display:flex;gap:12px;align-items:center}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:720px){.grid{grid-template-columns:1fr}.top{padding:16px}.table{overflow:auto}}
</style>"""
LOGIN_HTML = (
    """<!doctype html><meta charset='utf-8'><title>心晴助手 · 管理后台</title>"""
    + BASE_STYLE
    + """<main class='auth panel'><h1>心晴助手</h1><p class='muted'>危机预警管理后台</p><form method='post'><input name='username' placeholder='用户名' autofocus required><input name='password' type='password' placeholder='密码' required><button>登录</button></form><p class='muted'>还没有账户？<a href='/admin/register'>注册工作人员账户</a></p>{% if error %}<p class='critical'>{{error}}</p>{% endif %}</main>"""
)
REGISTER_HTML = (
    """<!doctype html><meta charset='utf-8'><title>注册账户</title>"""
    + BASE_STYLE
    + """<main class='auth panel'><h1>注册账户</h1><p class='muted'>新账户默认只有查看权限，管理员可后续调整。</p><form method='post'><input name='username' placeholder='用户名（3-40位）' required><input name='password' type='password' placeholder='密码（至少8位）' required><input name='password2' type='password' placeholder='确认密码' required><button>提交注册</button></form><p><a href='/admin/login'>返回登录</a></p>{% if error %}<p class='critical'>{{error}}</p>{% endif %}</main>"""
)
ADMIN_HTML = (
    """<!doctype html><meta charset='utf-8'><title>危机预警管理</title>"""
    + BASE_STYLE
    + """<div class='top'><strong>心晴助手 · 危机预警管理</strong><nav class='nav'><span>{{admin['username']}} · {{admin['role']}}</span>{% if 'manage_users' in permissions %}<a href='/admin/users'>用户权限</a>{% endif %}<a href='/admin/logout'>退出</a></nav></div><div class='wrap'><form class='filters' method='get'><input name='user_id' value='{{user_id}}' placeholder='按用户 ID 搜索'><select name='risk'><option value=''>全部风险</option>{% for v in ['critical','high','medium','low','unknown'] %}<option value='{{v}}' {% if risk==v %}selected{% endif %}>{{v}}</option>{% endfor %}</select><select name='status'><option value=''>全部处理状态</option>{% for v in ['new','in_progress','contacted','closed'] %}<option value='{{v}}' {% if status==v %}selected{% endif %}>{{v}}</option>{% endfor %}</select><button>筛选</button></form><div class='table'><table><tr><th>时间</th><th>用户 ID</th><th>风险</th><th>紧急</th><th>邮件</th><th>处理状态</th><th>操作</th></tr>{% for row in rows %}<tr><td>{{row['received_at']}}</td><td>{{row['user_id']}}</td><td class='{{row['risk_level']}}'>{{row['risk_level']}}</td><td>{{'是' if row['immediate_action'] else '否'}}</td><td><span class='badge'>{{row['email_status']}}</span></td><td><span class='badge'>{{row['handling_status']}}</span></td><td class='actions'><a href='/admin/alert/{{row['alert_id']}}'>查看详情</a></td></tr>{% else %}<tr><td colspan='7'>暂无预警记录</td></tr>{% endfor %}</table></div></div>"""
)
DETAIL_HTML = """<!doctype html><meta charset='utf-8'><title>预警详情</title><style>body{font-family:system-ui,Microsoft YaHei;background:#f4f6f8;margin:0;color:#25313d}.top{background:#195b9d;color:#fff;padding:16px 24px}.wrap{max-width:960px;margin:22px auto;padding:0 16px}.panel{background:#fff;padding:20px;margin-bottom:14px;border-radius:10px;box-shadow:0 10px 26px rgba(37,49,61,.06)}dt{font-weight:bold;margin-top:10px}dd{margin:3px 0;white-space:pre-wrap;word-break:break-word}textarea{width:100%;min-height:80px;box-sizing:border-box}select,button,input{padding:9px;margin-top:8px}button{background:#195b9d;color:#fff;border:0;border-radius:4px}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin:12px 0 4px}.metric{display:grid;place-items:center;gap:8px;padding:14px;border:1px solid #e6edf2;border-radius:12px;background:#fbfdff}.ring{--value:0;--color:#195b9d;width:104px;height:104px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--color) calc(var(--value)*1%),#e9eef2 0);position:relative}.ring::after{content:"";position:absolute;inset:10px;border-radius:50%;background:#fff}.ring strong{position:relative;z-index:1;font-size:20px;color:#25313d;text-align:center}.metric span{font-size:13px;color:#667680}.metric em{font-style:normal;font-size:12px;color:#8a98a3}.critical{color:#b42318}.high{color:#c2410c}.medium{color:#a16207}</style><div class='top'><a href='/admin' style='color:#fff'>← 返回列表</a></div><div class='wrap'><div class='panel'><h2>预警详情</h2><dl><dt>预警 ID</dt><dd>{{row['alert_id']}}</dd><dt>用户 ID</dt><dd>{{row['user_id']}}</dd><dt>接收时间</dt><dd>{{row['received_at']}}</dd><dt>风险等级</dt><dd class='{{row['risk_level']}}'>{{row['risk_level']}}</dd><dt>邮件状态</dt><dd>{{row['email_status']}}{% if row['email_error'] %}：{{row['email_error']}}{% endif %}</dd></dl></div><div class='panel'><h3>风险指标</h3><div class='metric-grid'>{% for item in metrics %}<div class='metric'><div class='ring' style='--value:{{item['value']}};--color:{{item['color']}}'><strong>{{item['text']}}</strong></div><span>{{item['label']}}</span><em>{{item['value']}}%</em></div>{% else %}<p>暂无可视化指标</p>{% endfor %}</div></div><div class='panel'><form method='post'><label>处理状态</label><br><select name='handling_status'>{% for v in ['new','in_progress','contacted','closed'] %}<option value='{{v}}' {% if row['handling_status']==v %}selected{% endif %}>{{v}}</option>{% endfor %}</select><br><input name='handled_by' value='{{row['handled_by'] or ""}}' placeholder='处理人'><br><textarea name='handling_note' placeholder='处理备注'>{{row['handling_note'] or ''}}</textarea><br><button>保存处理记录</button></form></div><div class='panel'><h3>预警数据</h3><pre>{{payload}}</pre></div></div>"""


def _admin_rows():
    init_db()
    risk, status, user_id = (
        request.args.get("risk", "").strip().lower(),
        request.args.get("status", "").strip().lower(),
        request.args.get("user_id", "").strip(),
    )
    query, values = "SELECT * FROM alerts WHERE 1=1", []
    if risk:
        query += " AND risk_level=%s"
        values.append(risk)
    if status:
        query += " AND handling_status=%s"
        values.append(status)
    if user_id:
        query += " AND user_id LIKE %s"
        values.append(f"%{user_id}%")
    query += " ORDER BY CASE risk_level WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, received_at DESC LIMIT 200"
    with _db() as conn:
        return conn.execute(query, values).fetchall()


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent(value: float | None, maximum: float) -> int:
    if value is None or maximum <= 0:
        return 0
    return max(0, min(100, round(value / maximum * 100)))


def _risk_metrics(payload: Mapping[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    risk = payload.get("risk_assessment") or {}
    if not isinstance(risk, Mapping):
        risk = {}
    level = str(row["risk_level"] or risk.get("risk_level", "unknown")).lower()
    level_map = {"low": 25, "medium": 50, "high": 75, "critical": 100}
    color_map = {"low": "#0f766e", "medium": "#ca8a04", "high": "#ea580c", "critical": "#b42318"}
    metrics: list[dict[str, Any]] = [
        {
            "label": "风险等级",
            "value": level_map.get(level, 0),
            "text": level,
            "color": color_map.get(level, "#64748b"),
        }
    ]
    stress = _number(risk.get("stress_level", row["stress_level"]))
    if stress is not None:
        metrics.append(
            {
                "label": "压力等级",
                "value": _percent(stress, 5),
                "text": f"{stress:g}/5",
                "color": "#2563eb" if stress < 3 else "#ea580c" if stress < 5 else "#b42318",
            }
        )
    for key, label in (
        ("confidence_level", "置信度"),
        ("confidence", "置信度"),
        ("risk_score", "风险分数"),
        ("score", "综合分数"),
    ):
        value = _number(risk.get(key))
        if value is None:
            continue
        maximum = 1 if value <= 1 else 100
        metrics.append(
            {
                "label": label,
                "value": _percent(value, maximum),
                "text": f"{_percent(value, maximum)}%",
                "color": "#7c3aed" if label == "置信度" else "#dc2626",
            }
        )
        if label == "置信度":
            break
    if row["immediate_action"]:
        metrics.append({"label": "立即介入", "value": 100, "text": "需要", "color": "#b42318"})
    return metrics


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with _db() as conn:
            user = conn.execute(
                "SELECT * FROM admin_users WHERE username=%s AND enabled=1", (username,)
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["admin_user_id"] = user["id"]
            session["admin_authenticated"] = True
            return redirect(request.args.get("next") or url_for("admin_index"))
        # Keep the legacy token usable during migration, but prefer accounts.
        if ADMIN_TOKEN and secrets.compare_digest(password, ADMIN_TOKEN):
            session["admin_authenticated"] = True
            return redirect(request.args.get("next") or url_for("admin_index"))
        error = "用户名或密码不正确。"
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if (
            len(username) < 3
            or len(username) > 40
            or not username.replace("_", "").replace("-", "").isalnum()
        ):
            error = "用户名需为 3-40 位字母、数字、下划线或短横线。"
        elif len(password) < 8:
            error = "密码至少需要 8 位。"
        elif password != request.form.get("password2", ""):
            error = "两次输入的密码不一致。"
        else:
            try:
                with _db() as conn:
                    now = _now()
                    conn.execute(
                        "INSERT INTO admin_users(username,password_hash,role,enabled,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
                        (username, generate_password_hash(password), "viewer", 1, now, now),
                    )
                return redirect(url_for("admin_login"))
            except UniqueViolation:
                error = "用户名已存在。"
    return render_template_string(REGISTER_HTML, error=error)


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin")
@require_permission("view_alerts")
def admin_index():
    admin = current_admin() or {"username": "token-admin", "role": "admin"}
    return render_template_string(
        ADMIN_HTML,
        rows=_admin_rows(),
        admin=admin,
        permissions=ROLE_PERMISSIONS.get(admin.get("role"), ROLE_PERMISSIONS["admin"]),
        risk=request.args.get("risk", ""),
        status=request.args.get("status", ""),
        user_id=request.args.get("user_id", ""),
    )


@app.route("/admin/alert/<alert_id>", methods=["GET", "POST"])
@require_permission("view_alerts")
def admin_detail(alert_id: str):
    row = _get_alert(alert_id)
    if row is None:
        return "未找到预警", 404
    if request.method == "POST":
        if not has_permission("manage_alerts"):
            return "没有处理告警的权限", 403
        status = request.form.get("handling_status", "new")
        if status not in {"new", "in_progress", "contacted", "closed"}:
            return "处理状态无效", 400
        with _db() as conn:
            conn.execute(
                "UPDATE alerts SET handling_status=%s,handled_by=%s,handling_note=%s,updated_at=%s WHERE alert_id=%s",
                (
                    status,
                    request.form.get("handled_by", "").strip(),
                    request.form.get("handling_note", "").strip(),
                    _now(),
                    alert_id,
                ),
            )
        row = _get_alert(alert_id)
    payload = json.loads(row["payload_json"])
    return render_template_string(
        DETAIL_HTML,
        row=row,
        metrics=_risk_metrics(payload, row),
        payload=json.dumps(payload, ensure_ascii=False, indent=2),
    )


@app.get("/api/admin/alerts")
@require_admin
def admin_api_alerts():
    return jsonify([dict(row) for row in _admin_rows()])


USERS_HTML = (
    """<!doctype html><meta charset='utf-8'><title>用户权限</title>"""
    + BASE_STYLE
    + """<div class='top'><strong>用户权限管理</strong><nav class='nav'><a href='/admin'>返回告警</a><a href='/admin/logout'>退出</a></nav></div><div class='wrap panel'><p class='muted'>管理员可分配权限：查看员只能查看，处理员可以更新告警，管理员可以管理用户。</p><table><tr><th>用户名</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th></tr>{% for row in users %}<tr><td>{{row['username']}}</td><td>{{row['role']}}</td><td>{{'启用' if row['enabled'] else '停用'}}</td><td>{{row['created_at']}}</td><td><form method='post' style='display:flex;gap:8px'><input type='hidden' name='user_id' value='{{row['id']}}'><select name='role'>{% for role in ['viewer','operator','admin'] %}<option value='{{role}}' {% if row['role']==role %}selected{% endif %}>{{role}}</option>{% endfor %}</select><select name='enabled'><option value='1' {% if row['enabled'] else '' %}>启用</option><option value='0' {% if not row['enabled'] else '' %}>停用</option></select><button>保存</button></form></td></tr>{% endfor %}</table></div>"""
)


@app.route("/admin/users", methods=["GET", "POST"])
@require_permission("manage_users")
def admin_users():
    if request.method == "POST":
        role = request.form.get("role", "viewer")
        enabled = request.form.get("enabled", "1")
        user_id = request.form.get("user_id", "")
        if role not in ROLE_PERMISSIONS or enabled not in {"0", "1"}:
            return "权限参数无效", 400
        with _db() as conn:
            conn.execute(
                "UPDATE admin_users SET role=%s,enabled=%s,updated_at=%s WHERE id=%s",
                (role, int(enabled), _now(), user_id),
            )
    with _db() as conn:
        users = conn.execute(
            "SELECT id,username,role,enabled,created_at FROM admin_users ORDER BY id"
        ).fetchall()
    return render_template_string(USERS_HTML, users=users)


init_db()


if __name__ == "__main__":
    port = int(os.getenv("ALERT_PORT", os.getenv("PORT", "5000")))
    app.run(host=os.getenv("ALERT_HOST", "0.0.0.0"), port=port, debug=False, threaded=True)
