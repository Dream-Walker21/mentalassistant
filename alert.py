"""HTTP alert receiver and pluggable alert delivery service.

Run this file as a small independent service. LangGraph posts to ``/alert``;
this module decides whether the payload is delivered by email, a webhook, or
just logged. Set ``ALERT_CHANNEL`` to switch channels without changing the
conversation workflow.
"""

from __future__ import annotations

import html
import json
import logging
import os
import smtplib
from abc import ABC, abstractmethod
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Mapping, Sequence
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import Flask, jsonify, request


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("xin-qing-alert")
app = Flask(__name__)


def _env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def _normalize_payload(data: Mapping[str, Any]) -> Dict[str, Any]:
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
    return payload


class AlertNotifier(ABC):
    """Delivery interface. Add another implementation without touching graph.py."""

    @abstractmethod
    def send(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class LogNotifier(AlertNotifier):
    def send(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        logger.warning("心理危机告警: %s", json.dumps(dict(payload), ensure_ascii=False))
        return {"channel": "log"}


class EmailNotifier(AlertNotifier):
    def __init__(self) -> None:
        self.server = os.getenv("SMTP_SERVER", "smtp.qq.com")
        self.port = int(os.getenv("SMTP_PORT", "465"))
        self.username = os.getenv("SMTP_USERNAME", os.getenv("EMAIL_ADDRESS", ""))
        self.password = os.getenv("SMTP_PASSWORD", os.getenv("EMAIL_PASSWORD", ""))
        self.from_email = os.getenv("SMTP_FROM", self.username)
        self.recipients = _env_list("ALERT_RECIPIENTS")

    def send(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        if not self.username or not self.password or not self.recipients:
            raise RuntimeError(
                "邮件通道缺少 SMTP_USERNAME/SMTP_PASSWORD/ALERT_RECIPIENTS 配置"
            )

        risk = payload.get("risk_assessment") or {}
        risk_level = str(risk.get("risk_level", "unknown")).lower()
        subject = {
            "critical": "紧急心理危机预警",
            "high": "高度心理风险预警",
            "medium": "中度心理风险提醒",
            "low": "心理状态关注提醒",
        }.get(risk_level, "心理状态评估通知")
        query = html.escape(str(payload.get("query", ""))[:500])
        signals = risk.get("risk_signals") or []
        signal_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in signals)
        body = f"""
        <html><body>
        <h2>{html.escape(subject)}</h2>
        <p><b>告警 ID：</b>{html.escape(str(payload.get('alert_id', 'unknown')))}</p>
        <p><b>用户 ID：</b>{html.escape(str(payload.get('user_id', 'anonymous')))}</p>
        <p><b>时间：</b>{html.escape(str(payload.get('timestamp', 'unknown')))}</p>
        <p><b>风险等级：</b>{html.escape(risk_level)}</p>
        <p><b>紧急程度：</b>{html.escape(str(risk.get('urgency', 'unknown')))}</p>
        <p><b>压力等级：</b>{html.escape(str(risk.get('stress_level', 'unknown')))} / 5</p>
        <p><b>风险信号：</b></p><ul>{signal_html or '<li>未提供</li>'}</ul>
        <p><b>输入摘要：</b></p>
        <blockquote>{query or '未提供'}</blockquote>
        <p>此邮件由心晴助手告警服务自动生成，请由具备资质的工作人员进行后续判断。</p>
        </body></html>
        """
        message = MIMEMultipart()
        message["From"] = self.from_email
        message["To"] = ", ".join(self.recipients)
        message["Subject"] = subject
        message.attach(MIMEText(body, "html", "utf-8"))
        with smtplib.SMTP_SSL(self.server, self.port, timeout=30) as server:
            server.login(self.username, self.password)
            server.send_message(message)
        return {"channel": "email", "recipients_count": len(self.recipients)}


class WebhookNotifier(AlertNotifier):
    def __init__(self, url: str) -> None:
        self.url = url

    def send(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
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
    return jsonify({"status": "healthy", "service": "xin-qing-alert", "channel": os.getenv("ALERT_CHANNEL", "email")})


@app.post("/alert")
def receive_alert() -> Any:
    try:
        data = request.get_json(silent=False)
        if not isinstance(data, Mapping):
            return jsonify({"status": "error", "error": "请求体必须是 JSON 对象"}), 400
        payload = _normalize_payload(data)
        result = notifier.send(payload)
        return jsonify({"status": "success", "alert_id": payload["alert_id"], **result})
    except json.JSONDecodeError as exc:
        return jsonify({"status": "error", "error": f"alert_data 不是有效 JSON: {exc}"}), 400
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("告警发送失败")
        return jsonify({"status": "error", "error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.getenv("ALERT_PORT", os.getenv("PORT", "5000")))
    app.run(host=os.getenv("ALERT_HOST", "0.0.0.0"), port=port, debug=False)
