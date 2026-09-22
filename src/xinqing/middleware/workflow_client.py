"""HTTP client for calling the LangGraph workflow service.

The middleware talks to the LangGraph dev server (default port 2024) over
HTTP so that heavy dependencies (langchain, sentence-transformers, chromadb)
stay in the workflow process and are never imported here.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import structlog

logger = structlog.get_logger("xinqing.middleware.workflow_client")

DEFAULT_API_URL = os.getenv("LANGGRAPH_API_URL", "http://127.0.0.1:2024")
ASSISTANT_ID = "xin_qing"
_TIMEOUT_SHORT = 10
_TIMEOUT_RUN = 60


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        logger.error("http_error", url=url, status=exc.code, detail=detail)
        raise RuntimeError(f"workflow HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        logger.error("url_error", url=url, reason=str(exc.reason))
        raise RuntimeError(f"workflow unreachable: {exc.reason}") from exc
    return json.loads(body.decode("utf-8"))


def create_thread(user_id: str, conversation_id: str) -> str:
    url = f"{DEFAULT_API_URL}/threads"
    payload = {"metadata": {"user_id": user_id, "conversation_id": conversation_id}}
    result = _post_json(url, payload, _TIMEOUT_SHORT)
    thread_id = result.get("thread_id")
    if not thread_id:
        raise RuntimeError(f"workflow did not return thread_id: {result}")
    logger.info("thread_created", thread_id=thread_id, user_id=user_id)
    return thread_id


def run_workflow(thread_id: str, query: str, user_id: str, conversation_id: str) -> dict[str, Any]:
    url = f"{DEFAULT_API_URL}/threads/{thread_id}/runs/wait"
    payload = {
        "assistant_id": ASSISTANT_ID,
        "input": {
            "query": query,
            "user_id": user_id,
            "conversation_id": conversation_id,
        },
        "config": {"configurable": {"thread_id": thread_id}},
    }
    result = _post_json(url, payload, _TIMEOUT_RUN)
    logger.info(
        "workflow_completed",
        thread_id=thread_id,
        user_id=user_id,
        intent=result.get("intent"),
    )
    return result


def check_health() -> bool:
    url = f"{DEFAULT_API_URL}/health"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SHORT) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.warning("health_check_failed", error=str(exc))
        return False
