"""Multi-modal middleware service — the sole backend entry for the frontend.

Phase 1 (skeleton): text-only passthrough to the LangGraph workflow.
ASR/TTS/auth are deferred to later phases — see docs/MIDDLEWARE_BUILD.md.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import structlog
from flask import Flask, jsonify, request

from ..common.logging_config import setup_logging
from . import workflow_client

setup_logging()
logger = structlog.get_logger("xinqing.middleware")

app = Flask(__name__)

_avatar_cache: dict[str, dict[str, Any]] = {}

DEFAULT_AVATAR_COMMAND: dict[str, Any] = {
    "version": 1,
    "action": "idle",
    "expression": "neutral",
    "gesture": "none",
    "intensity": 0.0,
    "duration_ms": 1000,
}

_AVATAR_WHITELIST: dict[str, set[str]] = {
    "action": {"idle", "greet", "listen", "comfort", "think", "encourage", "alert", "goodbye"},
    "expression": {"neutral", "gentle_smile", "concerned", "calm", "serious"},
    "gesture": {"none", "nod", "wave", "open_hands", "hand_on_heart", "point"},
}


def _validate_avatar_command(command: dict[str, Any]) -> bool:
    for field, allowed in _AVATAR_WHITELIST.items():
        value = command.get(field)
        if value is not None and value not in allowed:
            return False
    intensity = command.get("intensity")
    if intensity is not None:
        try:
            if not (0.0 <= float(intensity) <= 1.0):
                return False
        except (TypeError, ValueError):
            return False
    duration = command.get("duration_ms")
    if duration is not None:
        try:
            if not (500 <= int(duration) <= 10000):
                return False
        except (TypeError, ValueError):
            return False
    return True


@app.post("/chat")
def chat() -> Any:
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id", "anonymous")
    input_type = body.get("input_type", "text")
    conversation_id = body.get("conversation_id") or str(uuid.uuid4())

    if input_type == "audio":
        return (
            jsonify(
                {"status": "error", "error": "语音输入尚未支持", "error_code": "ASR_NOT_CONFIGURED"}
            ),
            501,
        )

    query = body.get("content", "")
    if not query:
        return (
            jsonify(
                {"status": "error", "error": "content 不能为空", "error_code": "INVALID_INPUT"}
            ),
            400,
        )

    try:
        thread_id = body.get("thread_id", "") or workflow_client.create_thread(
            user_id, conversation_id
        )
        result = workflow_client.run_workflow(thread_id, query, user_id, conversation_id)
    except Exception as exc:
        logger.error("workflow_call_failed", user_id=user_id, error=str(exc))
        return (
            jsonify({"status": "error", "error": "工作流调用失败", "error_code": "WORKFLOW_ERROR"}),
            502,
        )

    avatar_command = (
        _avatar_cache.pop(user_id, None) or result.get("avatar_command") or DEFAULT_AVATAR_COMMAND
    )

    return jsonify(
        {
            "status": "success",
            "data": {
                "text": result.get("response", ""),
                "audio_url": None,
                "avatar_command": avatar_command,
                "intent": result.get("intent", "daily_support"),
                "risk_level": (result.get("risk_assessment") or {}).get("risk_level", "low"),
            },
            "thread_id": thread_id,
        }
    )


@app.post("/avatar/command")
def avatar_command() -> Any:
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id", "anonymous")
    command = body.get("command", {})
    if not isinstance(command, dict) or not _validate_avatar_command(command):
        return (
            jsonify({"status": "error", "error": "无效的动作指令", "error_code": "INVALID_INPUT"}),
            400,
        )
    _avatar_cache[user_id] = command
    logger.info("avatar_command_cached", user_id=user_id, action=command.get("action"))
    return jsonify({"status": "success"})


@app.get("/health")
def health() -> Any:
    workflow_ok = workflow_client.check_health()
    return jsonify(
        {
            "status": "healthy" if workflow_ok else "degraded",
            "services": {
                "workflow": "reachable" if workflow_ok else "unreachable",
                "asr": "not_configured",
                "tts": "not_configured",
            },
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("MIDDLEWARE_PORT", "8000"))
    app.run(host=os.getenv("MIDDLEWARE_HOST", "127.0.0.1"), port=port, debug=False, threaded=True)
