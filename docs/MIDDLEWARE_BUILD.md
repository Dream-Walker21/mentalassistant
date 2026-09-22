# 中间层开发文档

> 状态：待开发。本文档是自包含的交接文档，可直接用于新对话开发。
> 负责人：王力涵
> 关联：`API_CONTRACT.md`、`AUTH_DESIGN.md`、`AGENTS.md`

---

## 1. 概述

中间层是多模态中间层服务，作为前端的**唯一后端入口**。职责：ASR（语音→文本）、调用工作流、TTS（文本→语音）、聚合响应。**不做业务判断**——所有对话逻辑在工作流里。

中间层代码放在 `src/xinqing/middleware/`（空包已建好，只有 `__init__.py`）。

---

## 2. 当前项目结构（重构后）

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
  data_service/        ← 数据服务
    app.py             ← 数据 API（端口 8001）
    alert.py           ← 告警服务（端口 5000）
  middleware/          ← 中间层（待建，当前只有 __init__.py）
```

**服务端口约定**：

| 服务 | 端口 | 启动方式 |
|---|---|---|
| 中间层 | 8000 | `python -m xinqing.middleware.app` |
| 工作流 (LangGraph) | 2024 | `langgraph dev` |
| 数据 API | 8001 | `python -m xinqing.data_service.app` |
| 告警服务 | 5000 | `python -m xinqing.data_service.alert` |
| 前端 (Vite) | 5173 | `npm run dev` |

---

## 3. 三个接口

### 3.1 POST /chat（核心接口）

前端发文本或语音，中间层调工作流后聚合返回。

**请求（文本）**：
```json
{
  "user_id": "user-xxx",
  "input_type": "text",
  "content": "最近有点焦虑"
}
```

**请求（语音）**：`multipart/form-data`，字段 `user_id` + `input_type="audio"` + `audio`（文件）。

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
  }
}
```

**处理流程**：
1. 若 `input_type == "audio"`：ASR 转文本
2. HTTP 调工作流（见 §5）
3. 从动作缓存取出 `avatar_command`（见 §3.2）
4. 若需要 TTS：文本转语音，生成 `audio_url`
5. 聚合返回

### 3.2 POST /avatar/command（工作流→中间层）

工作流在生成回复后，通过此接口把立绘动作发给中间层缓存。

**请求**：
```json
{
  "user_id": "user-xxx",
  "command": {
    "version": 1, "action": "comfort", "expression": "calm",
    "gesture": "hand_on_heart", "intensity": 0.4, "duration_ms": 1600
  }
}
```

**实现**：存入内存 dict `_avatar_cache[user_id] = command`。`/chat` 响应时取出并清空。

**动作白名单**（工作流已校验，中间层可做二次校验）：

| 字段 | 允许值 |
|---|---|
| `action` | `idle`, `greet`, `listen`, `comfort`, `think`, `encourage`, `alert`, `goodbye` |
| `expression` | `neutral`, `gentle_smile`, `concerned`, `calm`, `serious` |
| `gesture` | `none`, `nod`, `wave`, `open_hands`, `hand_on_heart`, `point` |
| `intensity` | 0.0 ~ 1.0 |
| `duration_ms` | 500 ~ 10000 |

### 3.3 GET /health

```json
{
  "status": "healthy",
  "services": {
    "workflow": "reachable",
    "asr": "not_configured",
    "tts": "not_configured"
  }
}
```

检查工作流可达性（GET `http://127.0.0.1:2024/health`）。ASR/TTS 在阶段 2 接入后改为 `ready`。

---

## 4. 技术选型

| 组件 | 选型 | 理由 |
|---|---|---|
| Web 框架 | **Flask** | 与现有 data_service/alert 一致，团队熟悉 |
| 工作流调用 | **HTTP 调 langgraph:2024** | 分进程部署，不 import 重型依赖 |
| 动作缓存 | **内存 dict** | 10 人并发，不需要 Redis |
| ASR | **阶段 2 再选** | 候选：Whisper（本地）/ 阿里云语音识别（云） |
| TTS | **edge-tts 或 GPT-SoVITS** | data_service 已预留 GPT-SoVITS 接口 |
| 鉴权 | **阶段 1 不做** | 推迟到中间层建完后按 `docs/AUTH_DESIGN.md` 落地 JWT |

---

## 5. 工作流调用方式

中间层通过 HTTP 调 LangGraph API（端口 2024），不进程内调用。

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

**响应**（对应 AppState 子集）：
```json
{
  "response": "我听见你现在的感受了……",
  "intent": "daily_support",
  "emotion": "焦虑",
  "avatar_command": {...},
  "risk_assessment": {"risk_level": "low", "risk_flag": false, "urgency": "低"},
  "alert_sent": false
}
```

工作流 URL 可通过环境变量 `LANGGRAPH_API_URL` 覆盖，默认 `http://127.0.0.1:2024`。

---

## 6. 文件结构

```
src/xinqing/middleware/
  __init__.py          ← 已存在（空）
  app.py               ← Flask app + 三个路由 + 动作缓存
  workflow_client.py   ← 调 langgraph 的 HTTP 客户端
  asr.py               ← ASR 适配器（阶段 2）
  tts.py               ← TTS 适配器（阶段 2）
```

阶段 1 只需 `app.py` + `workflow_client.py`。

---

## 7. 分阶段开发计划

### 阶段 1：骨架（文字链路透传）

**目标**：前端发文字 → 中间层 → 工作流 → 文字回复。不带 ASR/TTS/立绘/鉴权。

**要做的**：
- `middleware/app.py`：Flask app + `POST /chat` + `POST /avatar/command` + `GET /health`
- `middleware/workflow_client.py`：HTTP 调 langgraph（创建线程 + 运行工作流）
- 动作缓存：内存 dict，按 user_id 隔离
- `/chat` 文本模式：调工作流 → 取缓存动作 → 返回 `{text, avatar_command, intent, risk_level}`
- `/health`：检查工作流可达

**不做**：ASR、TTS、鉴权、语音文件管理。

**验证**：`curl POST /chat` 发文本，收到工作流回复 + 默认动作。

### 阶段 2：接入 ASR/TTS

- `asr.py`：ASR 适配器，`/chat` 支持 `input_type: "audio"`
- `tts.py`：TTS 适配器，`/chat` 响应带 `audio_url`
- 语音文件管理（生成 URL、临时文件清理）

### 阶段 3：接入立绘

- 确认 `POST /avatar/command` 收到工作流发的动作
- `/chat` 响应里带真实动作（而非默认）
- 前端用动作驱动 Live2D

### 阶段 4：危机路径联调

- 确认高危时工作流直接调告警服务（不经过中间层）
- 中间层容忍告警失败，不阻断回复

---

## 8. 代码骨架

### 8.1 workflow_client.py

```python
"""HTTP client for calling the LangGraph workflow service."""

from __future__ import annotations

import os
from typing import Any

import structlog
import urllib.request

logger = structlog.get_logger("xinqing.middleware.workflow_client")

DEFAULT_API_URL = os.getenv("LANGGRAPH_API_URL", "http://127.0.0.1:2024")


def create_thread(user_id: str, conversation_id: str) -> str:
    """Create a LangGraph thread and return its thread_id."""
    # POST {API_URL}/threads with metadata
    ...


def run_workflow(thread_id: str, query: str, user_id: str, conversation_id: str) -> dict[str, Any]:
    """Run the xin_qing graph and wait for the result."""
    # POST {API_URL}/threads/{thread_id}/runs/wait
    # assistant_id="xin_qing"
    # return response dict with keys: response, intent, avatar_command, risk_assessment, ...
    ...


def check_health() -> bool:
    """Check if the workflow service is reachable."""
    # GET {API_URL}/health
    ...
```

### 8.2 app.py

```python
"""Multi-modal middleware service — the sole backend entry for the frontend."""

from __future__ import annotations

import os
from typing import Any

import structlog
from flask import Flask, jsonify, request

from ..common.logging_config import setup_logging
from . import workflow_client

setup_logging()
logger = structlog.get_logger("xinqing.middleware")

app = Flask(__name__)

# In-memory avatar command cache, keyed by user_id.
_avatar_cache: dict[str, dict[str, Any]] = {}

DEFAULT_AVATAR_COMMAND = {
    "version": 1, "action": "idle", "expression": "neutral",
    "gesture": "none", "intensity": 0.0, "duration_ms": 1000,
}


@app.post("/chat")
def chat() -> Any:
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id", "anonymous")
    input_type = body.get("input_type", "text")
    conversation_id = body.get("conversation_id", "")

    if input_type == "audio":
        # Phase 2: ASR
        return jsonify({"status": "error", "error": "语音输入尚未支持"}), 501

    query = body.get("content", "")
    if not query:
        return jsonify({"status": "error", "error": "content 不能为空"}), 400

    try:
        thread_id = body.get("thread_id", "") or workflow_client.create_thread(user_id, conversation_id)
        result = workflow_client.run_workflow(thread_id, query, user_id, conversation_id)
    except Exception as exc:
        logger.error("workflow_call_failed", user_id=user_id, error=str(exc))
        return jsonify({"status": "error", "error": "工作流调用失败"}), 502

    avatar_command = _avatar_cache.pop(user_id, None) or result.get("avatar_command") or DEFAULT_AVATAR_COMMAND

    return jsonify({
        "status": "success",
        "data": {
            "text": result.get("response", ""),
            "audio_url": None,  # Phase 2: TTS
            "avatar_command": avatar_command,
            "intent": result.get("intent", "daily_support"),
            "risk_level": (result.get("risk_assessment") or {}).get("risk_level", "low"),
        },
        "thread_id": thread_id,
    })


@app.post("/avatar/command")
def avatar_command() -> Any:
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id", "anonymous")
    command = body.get("command", {})
    _avatar_cache[user_id] = command
    logger.info("avatar_command_cached", user_id=user_id, action=command.get("action"))
    return jsonify({"status": "success"})


@app.get("/health")
def health() -> Any:
    workflow_ok = workflow_client.check_health()
    return jsonify({
        "status": "healthy" if workflow_ok else "degraded",
        "services": {
            "workflow": "reachable" if workflow_ok else "unreachable",
            "asr": "not_configured",
            "tts": "not_configured",
        },
    })


if __name__ == "__main__":
    port = int(os.getenv("MIDDLEWARE_PORT", "8000"))
    app.run(host=os.getenv("MIDDLEWARE_HOST", "127.0.0.1"), port=port, debug=False, threaded=True)
```

---

## 9. 与现有服务的交互

```
前端(5173) ──→ 中间层(8000) ──→ 工作流(2024)
                   │
                   └─→ data_service(8001)  ← 阶段1前端可直连，鉴权阶段改由中间层代理

工作流(2024) ──→ 告警服务(5000)  ← 工作流内部直接调，不经过中间层
工作流(2024) ──→ 中间层(8000) /avatar/command  ← 工作流发立绘动作
```

**阶段 1 前端 Vite proxy 配置**（`web/live2d_demo/vite.config.js`）：

```js
proxy: {
  "/middleware-api": {
    target: "http://127.0.0.1:8000",
    rewrite: (path) => path.replace(/^\/middleware-api/, ""),
  },
  "/data-api": {
    target: "http://127.0.0.1:8001",
    rewrite: (path) => path.replace(/^\/data-api/, ""),
  },
  // 阶段1前端仍直连langgraph，阶段2改走中间层
  "/langgraph-api": {
    target: "http://127.0.0.1:2024",
    rewrite: (path) => path.replace(/^\/langgraph-api/, ""),
  },
}
```

> 骨架阶段前端可继续直连 data_service 和 langgraph，中间层只做 /chat。鉴权阶段（中间层建完后）前端改为只跟中间层说话。

---

## 10. 鉴权说明

**阶段 1-4 不做鉴权**。JWT 鉴权推迟到中间层建完后，按 `docs/AUTH_DESIGN.md` 落地。当前接口先不鉴权。

---

## 11. 环境变量

在 `.env.example` 中补充：

```
# Middleware
MIDDLEWARE_HOST=127.0.0.1
MIDDLEWARE_PORT=8000
LANGGRAPH_API_URL=http://127.0.0.1:2024
```

---

## 12. 验证方法

### 阶段 1 验证

```bash
# 1. 启动工作流
langgraph dev

# 2. 启动中间层
uv run python -m xinqing.middleware.app

# 3. 测试健康检查
curl http://127.0.0.1:8000/health

# 4. 测试文本对话
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test-user","input_type":"text","content":"最近有点焦虑"}'

# 5. 测试动作缓存
curl -X POST http://127.0.0.1:8000/avatar/command \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test-user","command":{"action":"comfort","expression":"calm"}}'
```

---

## 13. 开发注意事项

1. **日志**：用 `structlog.get_logger("xinqing.middleware")`，不要调 `setup_logging()`（中间层入口调一次即可）。
2. **异常不静默**：工作流调用失败返回 502，ASR/TTS 失败降级返回文字。
3. **不 import 工作流**：中间层通过 HTTP 调工作流，不 `from ..workflow.graph import build_graph`（会把 langchain/sentence-transformers 等重型依赖拉进来）。
4. **动作缓存是内存 dict**：进程重启会丢，前端用默认动作兜底。
5. **遵循 AGENTS.md**：改动前做影响评估，尤其触及 `common/data_layer.py`（L0）时。

---

*本文档自包含，可直接用于新对话开发中间层。*