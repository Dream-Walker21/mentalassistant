"""HTTP data service for the web client and future voice adapter.

Run independently from the LangGraph server. Both processes share the same
``DATA_DB_URL`` database, so the graph can resolve a user profile by user_id while
the frontend manages the user's profile and conversation history.
"""

from __future__ import annotations

import os
import secrets
from functools import wraps
from typing import Any, Callable

import structlog
from flask import Flask, jsonify, request

try:
    from .data_layer import DataStore, validate_user_id
    from .logging_config import setup_logging
except ImportError:
    from data_layer import DataStore, validate_user_id
    from logging_config import setup_logging


setup_logging()
logger = structlog.get_logger("xinqing.data_service")

app = Flask(__name__)
store = DataStore()
DATA_API_TOKEN = os.getenv("DATA_API_TOKEN", "").strip()


def require_api_token(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not DATA_API_TOKEN:
            return view(*args, **kwargs)
        token = request.headers.get("X-API-Key", "")
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token or not secrets.compare_digest(token, DATA_API_TOKEN):
            return jsonify({"status": "error", "error": "未授权"}), 401
        return view(*args, **kwargs)
    return wrapped


def payload() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return body


def bearer_token() -> str:
    value = request.headers.get("Authorization", "")
    return value[7:].strip() if value.lower().startswith("bearer ") else ""


@app.post("/api/auth/register")
@require_api_token
def register() -> Any:
    body = payload()
    user = store.register_user(body.get("nickname"), body.get("password"), body.get("real_name"), body.get("emergency_contacts"))
    token = store.create_session(user["user_id"])
    return jsonify({"status": "success", "token": token, "user": user}), 201


@app.post("/api/auth/login")
@require_api_token
def login() -> Any:
    body = payload()
    user = store.authenticate_user(body.get("nickname"), body.get("password"))
    if user is None:
        return jsonify({"status": "error", "error": "昵称或密码不正确"}), 401
    return jsonify({"status": "success", "token": store.create_session(user["user_id"]), "user": user})


@app.post("/api/auth/logout")
@require_api_token
def logout() -> Any:
    token = bearer_token()
    if token:
        store.revoke_session(token)
    return jsonify({"status": "success"})


@app.errorhandler(ValueError)
def bad_request(error: ValueError) -> Any:
    return jsonify({"status": "error", "error": str(error)}), 400


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "healthy", "service": "xin-qing-data", "tts_provider": "gpt-sovits", "tts_configured": bool(os.getenv("GPT_SOVITS_BASE_URL", "").strip())})


@app.route("/api/users/<user_id>", methods=["GET", "PUT", "DELETE"])
@require_api_token
def user_resource(user_id: str) -> Any:
    user_id = validate_user_id(user_id)
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
@require_api_token
def user_context(user_id: str) -> Any:
    return jsonify({"status": "success", "context": store.user_context(user_id)})


@app.route("/api/users/<user_id>/conversations", methods=["GET", "POST"])
@require_api_token
def conversations(user_id: str) -> Any:
    if request.method == "GET":
        return jsonify({"status": "success", "conversations": store.list_conversations(user_id, int(request.args.get("limit", 50)))})
    body = payload()
    conversation_id = store.ensure_conversation(user_id, body.get("conversation_id"), body.get("title", ""))
    return jsonify({"status": "success", "conversation_id": conversation_id}), 201


@app.route("/api/users/<user_id>/assessments", methods=["GET", "POST"])
@require_api_token
def assessments(user_id: str) -> Any:
    if request.method == "GET":
        return jsonify({"status": "success", "assessments": store.list_assessments(user_id, int(request.args.get("limit", 30)))})
    body = payload()
    assessment = body.get("assessment")
    if not isinstance(assessment, dict):
        raise ValueError("assessment 必须是对象")
    saved = store.save_assessment(user_id, str(body.get("assessment_type", "manual")), assessment, body.get("conversation_id"))
    return jsonify({"status": "success", "assessment": saved}), 201


@app.route("/api/conversations/<conversation_id>/messages", methods=["GET", "POST"])
@require_api_token
def messages(conversation_id: str) -> Any:
    if request.method == "GET":
        user_id = request.args.get("user_id")
        if not user_id:
            raise ValueError("需要 user_id 查询参数")
        return jsonify({"status": "success", "messages": store.list_messages(user_id, conversation_id, int(request.args.get("limit", 100)))})
    body = payload()
    if "user_id" not in body:
        raise ValueError("需要 user_id")
    message = store.add_message(body["user_id"], conversation_id, str(body.get("role", "")), body.get("content", ""), body.get("metadata"))
    return jsonify({"status": "success", "message": message}), 201


@app.post("/api/tts/synthesize")
@require_api_token
def tts_synthesize() -> Any:
    """Create a durable GPT-SoVITS request without coupling to its API yet."""
    body = payload()
    if "user_id" not in body:
        raise ValueError("需要 user_id")
    job = store.create_tts_job(body["user_id"], body.get("text", ""), body.get("conversation_id"), body.get("voice_profile", ""))
    job = store.update_tts_job(job["user_id"], job["job_id"], "not_configured", "尚未配置 GPT_SOVITS_BASE_URL 与适配器")
    return jsonify({
        "status": "not_configured",
        "job": job,
        "message": "已保留 GPT-SoVITS 任务接口，等待后续配置服务地址和请求适配器。",
    }), 501


@app.get("/api/users/<user_id>/tts/jobs/<job_id>")
@require_api_token
def tts_job(user_id: str, job_id: str) -> Any:
    job = store.get_tts_job(user_id, job_id)
    if job is None:
        return jsonify({"status": "error", "error": "未找到语音任务"}), 404
    return jsonify({"status": "success", "job": job})


if __name__ == "__main__":
    port = int(os.getenv("DATA_PORT", "8001"))
    app.run(host=os.getenv("DATA_HOST", "127.0.0.1"), port=port, debug=False, threaded=True)
