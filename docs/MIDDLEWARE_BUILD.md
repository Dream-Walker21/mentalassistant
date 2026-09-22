# data_service 兼中间层开发文档

> 状态：阶段1-2已完成（commit 8184d30，已 push origin+upstream）。阶段3-4待开发。
> 负责人：王力涵
> 关联：`API_CONTRACT.md`、`AUTH_DESIGN.md`、`AGENTS.md`

---

## 1. 架构决策

**不建独立中间层服务。** data_service（端口 8001）兼任中间层，既是数据 CRUD 服务，也是前端唯一后端入口。

**决策理由**：
- ASR 不做，TTS 是轻量 HTTP 调用（edge-tts 或 GPT-SoVITS 独立服务），中间层无重型依赖
- 工作流响应已含 `avatar_command`（AppState 有该字段），不需要 `/avatar/command` 缓存接口
- 合并后 data_service 是唯一面向前端的网关，鉴权天然统一（验 JWT），langgraph/alert 是下游（服务间 key）
- 少一个服务、少一个端口、少一层服务间鉴权

**架构图**：

```
前端(5173) ──→ data_service(8001) [验JWT] ──→ langgraph(2024) [服务间key]
                    │
                    ├── /api/auth/*（注册/登录/登出）
                    ├── /api/users/*（用户数据 CRUD）
                    ├── /api/conversations/*（对话历史）
                    ├── /api/tts/*（TTS）
                    └── /chat（调工作流 + TTS + 聚合）← 新增

langgraph(2024) ──→ alert(5000) [服务间key]（工作流内部调，不经过 data_service）
```

---

## 2. 当前项目结构

```
src/xinqing/
  common/              ← 共享基础设施
    config.py          ← DeepSeek/嵌入模型/URL 配置
    data_layer.py      ← DataStore（PostgreSQL）
    logging_config.py  ← structlog 配置
  workflow/            ← 智能体工作流
    graph.py           ← LangGraph 工作流
    prompts.py
    ingest.py          ← RAG 知识库构建
    app.py             ← langgraph 入口（端口 2024）
  data_service/        ← 数据服务 + 中间层（本文档目标）
    app.py             ← 数据 API + /chat（端口 8001）
    alert.py           ← 告警服务（端口 5000）
    workflow_client.py ← 调 langgraph 的 HTTP 客户端（待新增）
```

**服务端口约定**：

| 服务 | 端口 | 启动方式 |
|---|---|---|
| data_service（兼中间层） | 8001 | `python -m xinqing.data_service.app` |
| 工作流 (LangGraph) | 2024 | `langgraph dev` |
| 告警服务 | 5000 | `python -m xinqing.data_service.alert` |
| 前端 (Vite) | 5173 | `npm run dev` |

---

## 3. 新增接口：POST /chat

data_service/app.py 新增 `/chat` 路由，承担中间层职责。

**请求（文本）**：
```json
{
  "user_id": "user-xxx",
  "input_type": "text",
  "content": "最近有点焦虑",
  "conversation_id": "conv-xxx",
  "thread_id": "thread-xxx"
}
```

**响应**：
```json
{
  "status": "success",
  "data": {
    "text": "我听见你现在的感受了……",
    "audio_url": null,
    "avatar_command": {
      "version": 1, "action": "comfort", "expression": "calm",
      "gesture": "hand_on_heart", "intensity": 0.4, "duration_ms": 1600
    },
    "intent": "daily_support",
    "risk_level": "low"
  },
  "thread_id": "thread-xxx"
}
```

**处理流程**：
1. HTTP 调工作流（见 §4）
2. 从工作流响应取 `avatar_command`（已含在响应中，无需缓存接口）
3. 若需要 TTS：调 TTS 服务生成 `audio_url`（阶段 2）
4. 聚合返回

**去掉的接口**：`POST /avatar/command` 不再需要——工作流同步返回时已含 `avatar_command`。

---

## 4. 工作流调用方式

新增 `data_service/workflow_client.py`，HTTP 调 LangGraph API（端口 2024）。

**创建线程**（首次对话）：
```
POST http://127.0.0.1:2024/threads
{"metadata": {"user_id": "user-xxx", "conversation_id": "conv-xxx"}}
→ {"thread_id": "thread-xxx"}
```

**运行工作流**：
```
POST http://127.0.0.1:2024/threads/{thread_id}/runs/wait
{
  "assistant_id": "xin_qing",
  "input": {"query": "最近有点焦虑", "user_id": "user-xxx", "conversation_id": "conv-xxx"},
  "config": {"configurable": {"thread_id": "thread-xxx"}}
}
```

**响应**（AppState 子集，已含 avatar_command）：
```json
{
  "response": "我听见你现在的感受了……",
  "intent": "daily_support",
  "emotion": "焦虑",
  "avatar_command": {"version": 1, "action": "comfort", ...},
  "risk_assessment": {"risk_level": "low", "risk_flag": false, "urgency": "低"},
  "alert_sent": false
}
```

工作流 URL 可通过环境变量 `LANGGRAPH_API_URL` 覆盖，默认 `http://127.0.0.1:2024`。

---

## 5. 分阶段开发计划

### 阶段 1：骨架（文字链路透传）

**目标**：前端发文字 → data_service /chat → 工作流 → 文字回复。不带 TTS/鉴权。

**要做的**：
- `data_service/workflow_client.py`：HTTP 调 langgraph（创建线程 + 运行工作流）
- `data_service/app.py` 新增 `POST /chat` 路由：调工作流 → 返回 `{text, avatar_command, intent, risk_level}`
- `/health` 扩展：检查工作流可达

**不做**：TTS、鉴权、语音文件管理。

### 阶段 2：接入 TTS

- `/chat` 响应带 `audio_url`
- TTS 选型：edge-tts（轻量 HTTP）或 GPT-SoVITS（独立推理服务）
- 语音文件管理（生成 URL、临时文件清理）

### 阶段 3：接入 Live2D

- 工作流响应已含 `avatar_command`，前端直接用驱动 Live2D
- data_service 只需透传，无需额外处理

### 阶段 4：危机路径联调

- 确认高危时工作流直接调告警服务（不经过 data_service）
- data_service 容忍告警失败，不阻断回复

---

## 6. 代码骨架

### 6.1 workflow_client.py（新增文件）

```python
"""HTTP client for calling the LangGraph workflow service."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

import structlog

logger = structlog.get_logger("xinqing.data_service.workflow_client")

API_URL = os.getenv("LANGGRAPH_API_URL", "http://127.0.0.1:2024")


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{API_URL}{path}", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def create_thread(user_id: str, conversation_id: str) -> str:
    result = _post("/threads", {"metadata": {"user_id": user_id, "conversation_id": conversation_id}})
    return result.get("thread_id") or result.get("id") or ""


def run_workflow(thread_id: str, query: str, user_id: str, conversation_id: str) -> dict[str, Any]:
    return _post(f"/threads/{thread_id}/runs/wait", {
        "assistant_id": "xin_qing",
        "input": {"query": query, "user_id": user_id, "conversation_id": conversation_id},
        "config": {"configurable": {"thread_id": thread_id}},
    })


def check_health() -> bool:
    try:
        with urllib.request.urlopen(f"{API_URL}/ok", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False
```

### 6.2 app.py 新增路由（追加到现有 data_service/app.py）

```python
from . import workflow_client

DEFAULT_AVATAR_COMMAND = {
    "version": 1, "action": "idle", "expression": "neutral",
    "gesture": "none", "intensity": 0.0, "duration_ms": 1000,
}


@app.post("/chat")
def chat() -> Any:
    body = payload()
    user_id = body.get("user_id", "anonymous")
    input_type = body.get("input_type", "text")
    conversation_id = body.get("conversation_id", "")
    query = body.get("content", "")

    if not query:
        return jsonify({"status": "error", "error": "content 不能为空"}), 400

    try:
        thread_id = body.get("thread_id", "") or workflow_client.create_thread(user_id, conversation_id)
        result = workflow_client.run_workflow(thread_id, query, user_id, conversation_id)
    except Exception as exc:
        logger.error("workflow_call_failed", user_id=user_id, error=str(exc))
        return jsonify({"status": "error", "error": "工作流调用失败"}), 502

    return jsonify({
        "status": "success",
        "data": {
            "text": result.get("response", ""),
            "audio_url": None,  # Phase 2: TTS
            "avatar_command": result.get("avatar_command") or DEFAULT_AVATAR_COMMAND,
            "intent": result.get("intent", "daily_support"),
            "risk_level": (result.get("risk_assessment") or {}).get("risk_level", "low"),
        },
        "thread_id": thread_id,
    })
```

### 6.3 /health 扩展

```python
@app.get("/health")
def health() -> Any:
    workflow_ok = workflow_client.check_health()
    return jsonify({
        "status": "healthy" if workflow_ok else "degraded",
        "service": "xin-qing-data",
        "workflow": "reachable" if workflow_ok else "unreachable",
        "tts_provider": "gpt-sovits",
        "tts_configured": bool(os.getenv("GPT_SOVITS_BASE_URL", "").strip()),
    })
```

---

## 7. 前端改动（袁群负责）

前端 `web/live2d_demo/app.js` 改为只调 data_service：

**之前**（直连两个后端）：
```js
// /data-api/* → data_service:8001
// /langgraph-api/* → langgraph:2024
```

**之后**（只连 data_service）：
```js
// /data-api/* → data_service:8001（含 /chat）
```

`runGraph` 函数（app.js:115-133）改为调 `/data-api/chat`，不再直接调 langgraph。

**Vite proxy**（`web/live2d_demo/vite.config.js`）：去掉 `/langgraph-api` proxy。

---

## 8. 鉴权说明

**阶段 1-4 不做鉴权**。JWT 鉴权推迟到 /chat 链路跑通后，按 `docs/AUTH_DESIGN.md` 落地。data_service 是唯一面向前端的入口，JWT 在这里验；langgraph/alert 用服务间 key。

---

## 9. 环境变量

在 `.env.example` 中补充：

```
# Workflow API (for data_service to call langgraph)
LANGGRAPH_API_URL=http://127.0.0.1:2024
```

---

## 10. 验证方法

### 阶段 1 验证

```bash
# 1. 启动工作流
langgraph dev

# 2. 启动 data_service
uv run python -m xinqing.data_service.app

# 3. 测试健康检查
curl http://127.0.0.1:8001/health

# 4. 测试文本对话
curl -X POST http://127.0.0.1:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test-user","input_type":"text","content":"最近有点焦虑"}'
```

---

## 11. 开发注意事项

1. **日志**：用 `structlog.get_logger("xinqing.data_service")`，`setup_logging()` 已在 app.py 调过。
2. **异常不静默**：工作流调用失败返回 502，TTS 失败降级返回文字。
3. **不 import 工作流**：通过 HTTP 调 langgraph，不 `from ..workflow.graph import build_graph`（会把 langchain/sentence-transformers 等重型依赖拉进来）。
4. **avatar_command 透传**：工作流响应已含，data_service 直接取 `result.get("avatar_command")`，不需要缓存接口。
5. **遵循 AGENTS.md**：改动前做影响评估，尤其触及 `common/data_layer.py`（L0）时。

---

*本文档自包含，可直接用于新对话开发。*
