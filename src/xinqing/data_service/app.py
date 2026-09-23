"""HTTP data service for the web client and future voice adapter.

Run independently from the LangGraph server. Both processes share the same
``DATA_DB_URL`` database, so the graph can resolve a user profile by user_id while
the frontend manages the user's profile and conversation history.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

import structlog
from flask import Flask, g, jsonify, make_response, request, send_from_directory

from ..common.data_layer import DataStore, validate_user_id
from ..common.logging_config import setup_logging
from . import auth, tts, workflow_client

setup_logging()
logger = structlog.get_logger("xinqing.data_service")

app = Flask(__name__)
store = DataStore()

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/"

DEFAULT_AVATAR_COMMAND = {
    "version": 1,
    "action": "idle",
    "expression": "neutral",
    "gesture": "none",
    "intensity": 0.0,
    "duration_ms": 1000,
}


def require_auth(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        user_id = auth.verify_access_token(bearer_token())
        if not user_id:
            return (
                jsonify({"status": "error", "error": "未授权", "error_code": "UNAUTHORIZED"}),
                401,
            )
        g.user_id = user_id
        return view(*args, **kwargs)

    return wrapped


def _set_refresh_cookie(response: Any, token: str) -> Any:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        httponly=True,
        secure=os.getenv("FLASK_ENV") == "production",
        samesite="Strict",
        max_age=int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", "7")) * 86400,
        path=REFRESH_COOKIE_PATH,
    )
    return response


def _clear_refresh_cookie(response: Any) -> Any:
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
    return response


def _forbidden_if_mismatch(user_id: str) -> Any | None:
    if user_id != g.user_id:
        return (
            jsonify({"status": "error", "error": "无权访问", "error_code": "FORBIDDEN"}),
            403,
        )
    return None


def payload() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return body


def bearer_token() -> str:
    value = request.headers.get("Authorization", "")
    return value[7:].strip() if value.lower().startswith("bearer ") else ""


@app.post("/api/auth/register")
def register() -> Any:
    body = payload()
    user = store.register_user(
        body.get("nickname"),
        body.get("password"),
        body.get("real_name"),
        body.get("emergency_contacts"),
    )
    access_token = auth.create_access_token(user["user_id"])
    refresh_token = store.create_session(user["user_id"])
    response = make_response(
        jsonify({"status": "success", "access_token": access_token, "user": user}), 201
    )
    return _set_refresh_cookie(response, refresh_token)


@app.post("/api/auth/login")
def login() -> Any:
    body = payload()
    user = store.authenticate_user(body.get("nickname"), body.get("password"))
    if user is None:
        return jsonify({"status": "error", "error": "昵称或密码不正确"}), 401
    access_token = auth.create_access_token(user["user_id"])
    refresh_token = store.create_session(user["user_id"])
    response = make_response(
        jsonify({"status": "success", "access_token": access_token, "user": user})
    )
    return _set_refresh_cookie(response, refresh_token)


@app.post("/api/auth/refresh")
def refresh() -> Any:
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME, "")
    user_id = store.session_user(refresh_token)
    if not user_id:
        response = make_response(
            jsonify(
                {"status": "error", "error": "refresh token 无效", "error_code": "INVALID_REFRESH"}
            ),
            401,
        )
        return _clear_refresh_cookie(response)
    store.revoke_session(refresh_token)
    new_refresh = store.create_session(user_id)
    access_token = auth.create_access_token(user_id)
    response = make_response(jsonify({"status": "success", "access_token": access_token}))
    return _set_refresh_cookie(response, new_refresh)


@app.post("/api/auth/logout")
def logout() -> Any:
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME, "")
    if refresh_token:
        store.revoke_session(refresh_token)
    response = make_response(jsonify({"status": "success"}))
    return _clear_refresh_cookie(response)


@app.get("/api/auth/me")
@require_auth
def me() -> Any:
    user = store.public_user(g.user_id)
    return jsonify({"status": "success", "user": user})


@app.errorhandler(ValueError)
def bad_request(error: ValueError) -> Any:
    return jsonify({"status": "error", "error": str(error)}), 400


@app.get("/health")
def health() -> Any:
    workflow_ok = workflow_client.check_health()
    tts_ok = tts.check_ready()
    all_ok = workflow_ok and tts_ok
    return jsonify(
        {
            "status": "healthy" if all_ok else "degraded",
            "service": "xin-qing-data",
            "workflow": "reachable" if workflow_ok else "unreachable",
            "tts_provider": tts.PROVIDER,
            "tts_ready": tts_ok,
        }
    )


@app.get("/audio/<path:filename>")
def serve_audio(filename: str) -> Any:
    if not filename.lower().endswith((".mp3", ".wav")):
        return jsonify({"status": "error", "error": "不支持的音频格式"}), 404
    audio_dir = Path(tts.AUDIO_DIR)
    if not audio_dir.is_dir():
        return jsonify({"status": "error", "error": "音频目录不存在"}), 404
    return send_from_directory(str(audio_dir), filename)


@app.post("/chat")
@require_auth
def chat() -> Any:
    body = payload()
    user_id = g.user_id
    input_type = body.get("input_type", "text")
    conversation_id = body.get("conversation_id", "")
    query = body.get("content", "")

    if input_type != "text":
        return (
            jsonify({"status": "error", "error": "仅支持文本输入", "error_code": "INVALID_INPUT"}),
            400,
        )
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
            jsonify(
                {
                    "status": "error",
                    "error": "工作流调用失败",
                    "error_code": "WORKFLOW_ERROR",
                }
            ),
            502,
        )

    risk_assessment = result.get("risk_assessment") or {}
    text = result.get("response", "")
    audio_filename = tts.synthesize(text)
    audio_url = f"/audio/{audio_filename}" if audio_filename else None
    if text and not audio_filename:
        logger.warning("tts_degraded", user_id=user_id, provider=tts.PROVIDER)
    return jsonify(
        {
            "status": "success",
            "data": {
                "text": text,
                "audio_url": audio_url,
                "avatar_command": result.get("avatar_command") or DEFAULT_AVATAR_COMMAND,
                "intent": result.get("intent", "daily_support"),
                "risk_level": risk_assessment.get("risk_level", "low"),
            },
            "thread_id": thread_id,
        }
    )


@app.route("/api/users/<user_id>", methods=["GET", "PUT", "DELETE"])
@require_auth
def user_resource(user_id: str) -> Any:
    user_id = validate_user_id(user_id)
    denied = _forbidden_if_mismatch(user_id)
    if denied:
        return denied
    if request.method == "GET":
        # Keep sensitive registration fields server-side.  The browser only
        # needs the public profile to restore the chat shell.
        store.ensure_user(user_id)
        return jsonify({"status": "success", "user": store.public_user(user_id)})
    if request.method == "PUT":
        store.update_user(user_id, payload())
        return jsonify({"status": "success", "user": store.public_user(user_id)})
    deleted = store.delete_user(user_id)
    return jsonify({"status": "success", "deleted": deleted})


@app.get("/api/users/<user_id>/context")
@require_auth
def user_context(user_id: str) -> Any:
    user_id = validate_user_id(user_id)
    denied = _forbidden_if_mismatch(user_id)
    if denied:
        return denied
    return jsonify({"status": "success", "context": store.user_context(user_id)})


@app.route("/api/users/<user_id>/conversations", methods=["GET", "POST"])
@require_auth
def conversations(user_id: str) -> Any:
    user_id = validate_user_id(user_id)
    denied = _forbidden_if_mismatch(user_id)
    if denied:
        return denied
    if request.method == "GET":
        return jsonify(
            {
                "status": "success",
                "conversations": store.list_conversations(
                    user_id, int(request.args.get("limit", 50))
                ),
            }
        )
    body = payload()
    conversation_id = store.ensure_conversation(
        user_id, body.get("conversation_id"), body.get("title", "")
    )
    return jsonify({"status": "success", "conversation_id": conversation_id}), 201


@app.route("/api/users/<user_id>/assessments", methods=["GET", "POST"])
@require_auth
def assessments(user_id: str) -> Any:
    user_id = validate_user_id(user_id)
    denied = _forbidden_if_mismatch(user_id)
    if denied:
        return denied
    if request.method == "GET":
        return jsonify(
            {
                "status": "success",
                "assessments": store.list_assessments(user_id, int(request.args.get("limit", 30))),
            }
        )
    body = payload()
    assessment = body.get("assessment")
    if not isinstance(assessment, dict):
        raise ValueError("assessment 必须是对象")
    saved = store.save_assessment(
        user_id, str(body.get("assessment_type", "manual")), assessment, body.get("conversation_id")
    )
    return jsonify({"status": "success", "assessment": saved}), 201


@app.route("/api/conversations/<conversation_id>/messages", methods=["GET", "POST"])
@require_auth
def messages(conversation_id: str) -> Any:
    user_id = g.user_id
    if request.method == "GET":
        return jsonify(
            {
                "status": "success",
                "messages": store.list_messages(
                    user_id, conversation_id, int(request.args.get("limit", 100))
                ),
            }
        )
    body = payload()
    message = store.add_message(
        user_id,
        conversation_id,
        str(body.get("role", "")),
        body.get("content", ""),
        body.get("metadata"),
    )
    return jsonify({"status": "success", "message": message}), 201


@app.post("/api/tts/synthesize")
@require_auth
def tts_synthesize() -> Any:
    """Create a durable GPT-SoVITS request without coupling to its API yet."""
    body = payload()
    user_id = g.user_id
    job = store.create_tts_job(
        user_id,
        body.get("text", ""),
        body.get("conversation_id"),
        body.get("voice_profile", ""),
    )
    job = store.update_tts_job(
        job["user_id"], job["job_id"], "not_configured", "尚未配置 GPT_SOVITS_BASE_URL 与适配器"
    )
    return jsonify(
        {
            "status": "not_configured",
            "job": job,
            "message": "已保留 GPT-SoVITS 任务接口，等待后续配置服务地址和请求适配器。",
        }
    ), 501


@app.get("/api/users/<user_id>/tts/jobs/<job_id>")
@require_auth
def tts_job(user_id: str, job_id: str) -> Any:
    user_id = validate_user_id(user_id)
    denied = _forbidden_if_mismatch(user_id)
    if denied:
        return denied
    job = store.get_tts_job(user_id, job_id)
    if job is None:
        return jsonify({"status": "error", "error": "未找到语音任务"}), 404
    return jsonify({"status": "success", "job": job})


if __name__ == "__main__":
    port = int(os.getenv("DATA_PORT", "8001"))
    app.run(host=os.getenv("DATA_HOST", "127.0.0.1"), port=port, debug=False, threaded=True)
