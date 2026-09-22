"""HTTP client for calling the LangGraph workflow service.

data_service calls the workflow over HTTP (urllib) instead of importing
``workflow.graph`` to avoid pulling langchain / sentence-transformers / torch
into the data service process.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

import structlog

logger = structlog.get_logger("xinqing.data_service.workflow_client")

API_URL = os.getenv("LANGGRAPH_API_URL", "http://127.0.0.1:2024").rstrip("/")
ASSISTANT_ID = os.getenv("LANGGRAPH_ASSISTANT_ID", "xin_qing")


def _post(path: str, body: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{API_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def create_thread(user_id: str, conversation_id: str) -> str:
    result = _post(
        "/threads",
        {"metadata": {"user_id": user_id, "conversation_id": conversation_id}},
        timeout=10.0,
    )
    return result.get("thread_id") or result.get("id") or ""


def run_workflow(thread_id: str, query: str, user_id: str, conversation_id: str) -> dict[str, Any]:
    return _post(
        f"/threads/{thread_id}/runs/wait",
        {
            "assistant_id": ASSISTANT_ID,
            "input": {
                "query": query,
                "user_id": user_id,
                "conversation_id": conversation_id,
            },
            "config": {"configurable": {"thread_id": thread_id}},
        },
    )


def check_health() -> bool:
    try:
        with urllib.request.urlopen(f"{API_URL}/ok", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False
