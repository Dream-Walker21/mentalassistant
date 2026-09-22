# 心晴助手 前后端接口契约文档

> 本文档定义「智能体工作流」「后端数据处理与多模态」「Web 前端」三部分之间的接口契约。
> 所有接口以 HTTP + JSON 通信，编码统一 UTF-8。
> 契约基于已实现的 `src/xinqing/graph.py`、`src/xinqing/alert.py` 行为定义，后续改动需同步更新本文档并通知对接人。

---

## 1. 系统架构与请求流转

### 1.1 三部分职责

| 编号 | 部分 | 负责人 | 作用 |
|---|---|---|---|
| ① | 智能体工作流 | 蒋状钊 | 意图分类、情绪识别、RAG 检索、风险评估、告警、立绘动作决策 |
| ② | 数据服务（兼中间层） | 王力涵 | 数据 CRUD、调用工作流、TTS 文本转语音、聚合响应 |
| ③ | Web 前端 | 袁群 | 数字人立绘、文本/语音输入、展示语音+文本+动作 |

### 1.2 一次完整对话的请求流转

```
用户
  │  (文本 或 语音)
  ▼
┌─────────────────┐
│  ③ Web 前端     │
└────────┬────────┘
         │  接口 A: POST /chat
         ▼
┌─────────────────┐
│ ② data_service  │
│  (兼中间层)     │
└────────┬────────┘
         │  接口 B: 调用工作流 (HTTP)
         ▼
┌─────────────────┐
│ ① 智能体工作流  │  (LangGraph)
│                 │
│  ├─ daily       │
│  ├─ assessment  │
│  └─ crisis ─────┼──► 接口 D: POST /alert  → 告警服务 (邮件/webhook)
│                 │
│  返回 {文本回复, 动作指令, 风险评估, ...}
└────────┬────────┘
         │  data_service 聚合 {语音, 文本, 动作}
         ▼
┌─────────────────┐
│  ③ Web 前端     │  播放语音 + 显示文本 + 驱动立绘动作
└─────────────────┘
```

### 1.3 关键说明

- **工作流是核心**：所有对话逻辑由 ① 完成，② 只做模态转换和聚合，不做业务判断。
- **立绘动作的流向**：工作流响应已含 `avatar_command`，data_service 透传给前端，前端驱动 Live2D。无需独立缓存接口。
- **告警是旁路**：危机路径中工作流直接调用告警服务（接口 D），不经过 data_service，不阻塞对话回复。
- **前端永远只跟 data_service 说话**：前端不直接访问工作流或告警服务。

---

## 2. 端口与服务约定

| 服务 | 默认端口 | 启动方式 | 说明 |
|---|---|---|---|
| 智能体工作流 (LangGraph) | 2024 | `langgraph dev` 或自定义服务 | Agent Server，data_service 通过 HTTP 调用 |
| data_service（兼中间层） | 8001 | `python -m xinqing.data_service.app` | 前端唯一入口；数据 CRUD + 调工作流 + TTS + 聚合 |
| 告警服务 | 5000 | `python -m xinqing.data_service.alert` | 已实现，接收危机告警并发邮件/webhook |
| Web 前端 | 5173 或 3000 | `npm run dev`（待开发） | 用户浏览器访问 |

> 生产环境部署时端口可通过环境变量覆盖，开发阶段使用上述默认值。

---

## 3. 接口清单总览

| 接口 | 方向 | 方法 & 路径 | 状态 |
|---|---|---|---|
| A | 前端 → data_service | `POST /chat` | 待开发（王力涵） |
| A-health | 前端 → data_service | `GET /health` | ✅ 已实现 |
| B | data_service → 工作流 | HTTP 调 langgraph API | ✅ langgraph dev 已暴露 |
| D | 工作流 → 告警服务 | `POST /alert` | ✅ 已实现（`src/xinqing/data_service/alert.py`） |
| D-health | 工作流 → 告警服务 | `GET /health` | ✅ 已实现 |

---

## 4. 接口详细定义

### 接口 A：前端 → data_service（用户发送消息）

这是前端调用的唯一业务接口。data_service 负责调用工作流、将回复转语音后聚合返回。

```
POST /chat
Content-Type: multipart/form-data 或 application/json
```

#### 请求（文本输入）

```json
{
  "user_id": "student-001",
  "input_type": "text",
  "content": "最近有点焦虑，学习压力很大"
}
```

#### 请求（语音输入）

使用 `multipart/form-data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `user_id` | string | 用户标识 |
| `input_type` | string | 固定 `"audio"` |
| `audio` | file | 语音文件（wav/mp3/webm） |

#### 响应

```json
{
  "status": "success",
  "data": {
    "text": "我听见你现在的感受了……（工作流生成的文本回复）",
    "audio_url": "/audio/resp-20260915-1430-a1b2.mp3",
    "avatar_command": {
      "version": 1,
      "action": "comfort",
      "expression": "calm",
      "gesture": "hand_on_heart",
      "intensity": 0.4,
      "duration_ms": 1600
    },
    "intent": "daily_support",
    "risk_level": "low"
  }
}
```

#### 字段说明

| 字段 | 类型 | 必含 | 说明 |
|---|---|---|---|
| `data.text` | string | 是 | 工作流生成的文字回复，前端同时显示为字幕 |
| `data.audio_url` | string | 否 | TTS 生成的语音文件 URL；文本模式可不含 |
| `data.avatar_command` | object | 是 | 立绘动作指令，见 [数据模型-AvatarCommand](#avatarcommand) |
| `data.intent` | string | 是 | 意图分类结果：`daily_support` / `condition_judgement` / `crisis` |
| `data.risk_level` | string | 是 | 风险等级：`low` / `medium` / `high` / `critical`；非危机路径为 `low` |

#### 错误响应

```json
{
  "status": "error",
  "error": "语音识别失败：音频格式不支持",
  "error_code": "ASR_FAILED"
}
```

---

### 接口 A-health：中间层健康检查

```
GET /health
```

```json
{
  "status": "healthy",
  "services": {
    "workflow": "reachable",
    "asr": "ready",
    "tts": "ready"
  }
}
```

---

### 接口 B：中间层 → 智能体工作流（调用对话工作流）

中间层将 ASR 转出的文本（或原始文本）发给工作流，获取回复、动作指令、风险评估。

> 当前 `src/xinqing/graph.py` 已提供 `invoke_graph()` 函数可直接在进程内调用。
> 若中间层与工作流分进程部署，则需在工作流侧暴露一个 HTTP 接口（下方定义）。

#### 方式一：进程内调用（推荐开发阶段）

```python
from graph import build_graph
from ingest import load_retrievers

retrievers = load_retrievers()
graph = build_graph(
    retrievers=retrievers,
    avatar_http_url="http://127.0.0.1:8000/avatar/command",
    alert_http_url="http://127.0.0.1:5000/alert",
    checkpointer=MemorySaver(),
)

result = invoke_graph(
    graph,
    {"query": "最近有点焦虑，学习压力很大", "user_id": "student-001"},
    thread_id="student-001",
)
```

#### 方式二：HTTP 调用（推荐生产部署）

```
POST http://127.0.0.1:2024/invoke
Content-Type: application/json
```

```json
{
  "query": "最近有点焦虑，学习压力很大",
  "user_id": "student-001",
  "thread_id": "student-001"
}
```

#### 响应（对应 AppState 的子集）

```json
{
  "response": "我听见你现在的感受了……",
  "intent": "daily_support",
  "emotion": "焦虑",
  "avatar_command": {
    "version": 1,
    "action": "comfort",
    "expression": "calm",
    "gesture": "hand_on_heart",
    "intensity": 0.4,
    "duration_ms": 1600
  },
  "risk_assessment": {
    "risk_level": "low",
    "risk_flag": false,
    "urgency": "低"
  },
  "alert_sent": false
}
```

#### 字段说明

| 字段 | 类型 | 必含 | 说明 |
|---|---|---|---|
| `response` | string | 是 | 最终文本回复（对应 AppState.response） |
| `intent` | string | 是 | `daily_support` / `condition_judgement` / `crisis` |
| `emotion` | string | 否 | 情绪标签（仅 daily_support 路径有） |
| `avatar_command` | object | 是 | 立绘动作指令，工作流已做白名单校验 |
| `risk_assessment` | object | 否 | 风险评估结果（crisis / assessment 路径有） |
| `alert_sent` | bool | 否 | 是否已发送告警（仅 crisis 路径有） |

#### thread_id 与对话记忆

- `thread_id` 用于 LangGraph checkpointer 保留多轮历史，**同一个用户的每次请求传同一个 thread_id**（建议直接用 `user_id`）。
- 技术指标要求记忆 ≥10 轮，当前 `_history()` 默认取最近 25 条消息，满足要求。

---

### 接口 C：~~工作流 → 中间层（立绘动作指令）~~ 已废弃

> **已废弃**：工作流响应（接口 B 返回值）已含 `avatar_command` 字段，data_service 直接透传给前端，无需独立缓存接口。以下动作白名单仍有效，供前端/data_service 二次校验参考。

#### 动作白名单（由工作流保证，前端3&%5B.可做二次校:=校验）

| 字段 | 允许值 |
|---|---|
| `action` | `idle`, `greet`, `listen`, `comfort`, `think`, `encourage`, `alert`, `goodbye` |
| `expression` | `neutral`, `gentle_smile`, `concerned`, `calm`, `serious` |
| `gesture` | `none`, `nod`, `wave`, `open_hands`, `hand_on_heart`, `point` |
| `intensity` | 0.0 ~ 1.0 |
| `duration_ms` | 500 ~ 10000 |

> 危机/高风险状态下，工作流会强制禁止 `gentle_smile` 表情和 `wave` 手势，改用 `concerned`/`serious`。

---

### 接口 D：工作流 → 告警服务（危机告警）✅ 已实现

工作流在危机路径且风险等级达标时，自动调用此接口。**已由 `src/xinqing/alert.py` 实现**，无需重复开发。

```
POST http://127.0.0.1:5000/alert
Content-Type: application/json
```

#### 请求

```json
{
  "alert_data": {
    "user_id": "student-001",
    "query": "我不想活了，太累了",
    "risk_assessment": {
      "emotional_state": "抑郁",
      "stress_level": 5,
      "urgency": "高",
      "risk_flag": true,
      "risk_level": "critical",
      "risk_signals": ["疑似自伤/自杀表达"]
    },
    "timestamp": "2026-09-15 14:30:00"
  }
}
```

#### 响应

```json
{
  "status": "success",
  "alert_id": "alert-a1b2c3d4e5f6",
  "channel": "email"
}
```

#### 告警触发条件（工作流内部判断）

当以下任一为真时触发：
- `risk_flag == true`
- `risk_level` 为 `critical` 或 `high`
- `urgency` 为 `高`

#### 通道配置（环境变量）

| 环境变量 | 说明 |
|---|---|
| `ALERT_CHANNEL` | `email`（默认）/ `log` / `webhook` |
| `SMTP_USERNAME` + `SMTP_PASSWORD` | 邮箱账号 + SMTP 授权码 |
| `ALERT_RECIPIENTS` | 收件人，逗号分隔（辅导员/心理中心邮箱） |

---

## 5. 数据模型

### AvatarCommand

立绘动作指令，工作流输出，前端消费。

```typescript
interface AvatarCommand {
  version: 1;                    // 协议版本
  action: string;                // 见动作白名单
  expression: string;            // 见动作白名单
  gesture: string;               // 见动作白名单
  intensity: number;             // 0.0 ~ 1.0，动作强度
  duration_ms: number;           // 500 ~ 10000，动作持续毫秒数
}
```

### RiskAssessment

风险评估结果，危机路径和状态评估路径产出。

```typescript
interface RiskAssessment {
  emotional_state: string;       // 开心|平静|焦虑|抑郁|愤怒|其他
  stress_level: number;          // 1 ~ 5
  main_issues: string[];         // 学业压力|人际关系|家庭问题|自我认同|未来规划|其他
  urgency: string;               // 低|中|高
  support_needs: string[];       // 情感支持|实用建议|专业转介|紧急干预|其他
  risk_flag: boolean;            // 是否触发风险标记
  risk_level: string;            // low|medium|high|critical
  risk_signals: string[];        // 具体风险信号描述
  confidence_level: number;      // 0.0 ~ 1.0
}
```

### ChatRequest（接口 A 请求）

```typescript
interface ChatRequest {
  user_id: string;               // 用户标识，匿名时为 "anonymous"
  input_type: "text" | "audio";  // 输入模态
  content?: string;              // input_type="text" 时提供
  // input_type="audio" 时通过 multipart file 字段 "audio" 提供
}
```

### ChatResponse（接口 A 响应）

```typescript
interface ChatResponse {
  status: "success" | "error";
  data?: {
    text: string;                // 文字回复
    audio_url?: string;          // 语音文件 URL（TTS 产出）
    avatar_command: AvatarCommand;
    intent: string;              // daily_support|condition_judgement|crisis
    risk_level: string;          // low|medium|high|critical
  };
  error?: string;
  error_code?: string;
}
```

---

## 6. 错误处理约定

### 6.1 统一错误格式

所有接口错误响应统一为：

```json
{
  "status": "error",
  "error": "人类可读的错误描述",
  "error_code": "ERROR_CODE"
}
```

### 6.2 错误码约定

| error_code | 含义 | 处理建议 |
|---|---|---|
| `ASR_FAILED` | 语音识别失败 | 提示用户重试或改用文本输入 |
| `TTS_FAILED` | 语音合成失败 | 仍返回文字回复，仅语音缺失 |
| `WORKFLOW_TIMEOUT` | 工作流响应超时 | 前端显示「助手正在思考，请稍候」 |
| `WORKFLOW_ERROR` | 工作流内部错误 | 返回兜底回复，记录日志 |
| `INVALID_INPUT` | 输入参数校验失败 | 前端不应发送，属开发期 bug |
| `ALERT_FAILED` | 告警发送失败 | 不影响用户端回复，仅记录日志 |

### 6.3 容错原则

| 场景 | 处理 |
|---|---|
| 立绘动作发送失败 | 不阻断文字回复，前端用默认动作 |
| 告警发送失败 | 不阻断危机回复，用户仍收到支持性文字 |
| TTS 失败 | 返回文字 + 默认动作，无语音 |
| 工作流超时 | 中间层返回超时错误，前端提示重试 |
| 知识库不可用 | 工作流已有兜底（`_retrieve` 返回提示），不中断对话 |

---

## 7. 安全约定

1. **API Key 不进前端**：DeepSeek 密钥、SMTP 密码只在后端环境变量中配置。
2. **用户匿名**：`user_id` 不绑定真实身份，前端可生成随机 ID 存 localStorage。
3. **告警脱敏**：告警邮件中的用户输入截断为前 500 字（`src/xinqing/alert.py` 已实现）。
4. **非诊断声明**：所有回复不构成医学诊断，风险评估仅作参考信号。
5. **危机安全边界**：工作流不提供自伤方法、不做诊断；高风险场景鼓励联系心理热线 `025-58255200` 或急救服务。

---

## 8. 各端开发 checklist

### 王力涵（data_service 兼中间层）

- [ ] 实现 `POST /chat`：接收文本 → 调用工作流 → TTS → 聚合响应（含 avatar_command）
- [ ] 实现 `GET /health`：检查工作流/TTS 可达性
- [ ] TTS 选型（如 edge-tts / GPT-SoVITS）
- [ ] 语音文件管理（生成 URL，定期清理临时文件）
- [ ] 超时与错误处理

### 袁群（Web 前端）

- [ ] 文本输入框
- [ ] 调用 `POST /chat`
- [ ] 数字人立绘：根据 `avatar_command` 驱动表情/手势/动作
- [ ] 文字回复展示（逐字或气泡）
- [ ] 语音播放（`audio_url`）
- [ ] 对话历史展示
- [ ] 危机提示样式（`risk_level` 为 high/critical 时特殊展示）
- [ ] 错误态处理（超时等）

### 蒋状钊（智能体工作流，已基本完成）

- [ ] 补齐 3 个知识库 PDF 文件
- [ ] 跑通 `src/xinqing/ingest.py` 建 Chroma 索引
- [ ] 端到端测试三条分支（daily / assessment / crisis）
- [ ] 确认 DeepSeek 模型名可用
- [ ] （可选）暴露工作流 HTTP 接口供 data_service 远程调用
- [ ] （可选）接入真实节假日 API

---

## 9. 联调顺序建议

```
第1步  蒋状钊: 补齐知识库 + 工作流端到端跑通（可独立完成）
        ↓
第2步  三人共同确认本契约文档无异议
        ↓
第3步  王力涵: 在 data_service 实现 /chat 骨架（先不做 TTS，直接透传文本调工作流）
        袁群: 实现前端骨架（文本输入 + 文字回复展示，先不做立绘）
        → 此时可三方联调：前端发文字 → data_service → 工作流 → 文字回复
        ↓
第4步  王力涵: 接入 TTS
        袁群: 接入语音播放
        → 联调语音链路
        ↓
第5步  袁群: 接入数字人立绘（avatar_command 驱动，工作流响应已含）
        → 联调立绘动作
        ↓
第6步  全员: 危机路径联调（触发告警邮件 + 危机回复 + 严肃表情动作）
```

---

*文档维护：接口变更时更新本文档并通知所有对接人。*