# 心晴助手 项目协作规则

> 本文件是项目级 AGENTS.md，优先级高于全局规则。所有协作者（含 AI）在改动代码前必须遵守。

---

## 1. 团队与分工

| 成员 | 职责 | 负责目录 |
|---|---|---|
| 蒋状钊 | ① 智能体工作流（LangGraph） | `src/xinqing/workflow/`（graph.py、prompts.py、ingest.py、app.py）、`src/xinqing/common/config.py` |
| 王力涵 | ② 多模态中间层 + 数据层 + 部署 | `src/xinqing/common/data_layer.py`、`src/xinqing/data_service/`（app.py、alert.py）、`src/xinqing/middleware/`（待建）、`scripts/` |
| 袁群 | ③ Web 前端 | `web/`（正式前端待建，当前仅有 `web/live2d_demo/` 测试面板） |
| 共同维护 | 接口契约、项目结构、文档 | `docs/API_CONTRACT.md`、`AGENTS.md`、`pyproject.toml`、`.env.example` |

---

## 2. 模块依赖图

改动前必须对照此图评估影响范围。箭头 `A → B` 表示 A 依赖 B。

```
L2 服务入口
  workflow/app.py ──→ workflow/graph.py
    └──→ workflow/ingest.py

  data_service/app.py ──→ common/data_layer.py
  data_service/alert.py ──→ common/data_layer.py
  middleware/(待建) ──→ workflow/graph.py (invoke_graph)

L1 核心逻辑
  workflow/graph.py ──→ workflow/prompts.py
    ──→ common/config.py
    ──→ common/data_layer.py
    ──→ workflow/ingest.py ──→ [chromadb, sentence-transformers, modelscope]

L0 基础设施
  common/config.py ──→ [环境变量, langchain_deepseek]
  common/data_layer.py ──→ [psycopg, werkzeug.security]
```

### 层次定义

| 层 | 模块 | 改动风险 |
|---|---|---|
| L0 基础设施 | `common/config.py`、`common/data_layer.py` | **最高** — 所有上游模块都依赖 |
| L1 核心逻辑 | `workflow/prompts.py`、`workflow/ingest.py`、`workflow/graph.py` | **高** — 工作流核心，接口变动影响中间层和前端 |
| L2 服务入口 | `workflow/app.py`、`data_service/app.py`、`data_service/alert.py`、`middleware/` | **中** — 对外暴露 HTTP 接口 |
| L3 前端 | `web/` | **低** — 仅影响用户界面 |

---

## 3. 接口边界清单

以下为**跨人协作的公共契约**，改动必须通知所有受影响方并更新 `docs/API_CONTRACT.md`：

| 边界 | 定义位置 | 消费方 |
|---|---|---|
| `build_graph()` / `invoke_graph()` 签名 | `workflow/graph.py` | 中间层、`workflow/app.py` |
| `DataStore` 公共方法 | `common/data_layer.py` | `workflow/graph.py`、`data_service/alert.py`、`data_service/app.py` |
| `POST /alert` 接口 | `data_service/alert.py` | `workflow/graph.py`（危机路径调用） |
| `/api/*` 数据接口 | `data_service/app.py` | 前端（待建） |
| `POST /chat` 接口 | `middleware/`（待建） | 前端（待建） |
| `POST /avatar/command` 接口 | `middleware/`（待建） | `workflow/graph.py`（工作流调用） |
| `AvatarCommand` 数据模型 | `API_CONTRACT.md` §5 | 工作流、中间层、前端 |
| `RiskAssessment` 数据模型 | `API_CONTRACT.md` §5 | 工作流、中间层、前端 |
| 环境变量集合 | `.env.example` | 所有服务 |
| 动作白名单 | `API_CONTRACT.md` §4 接口C | 工作流（产出）、前端（消费） |

---

## 4. 改动影响评估流程（强制）

**每次改动前，AI 必须执行以下评估并向用户报告，用户确认后才动手。**

### 步骤

```
1. 定位：改动涉及哪些文件？属于哪个层次（L0-L3）？
2. 查依赖图：沿箭头反向追溯，找出所有受影响的上游模块。
3. 判边界：改动是否触及 §3 接口边界清单中的任一项？
4. 定影响：
     - 不触及边界 → 内部改动，可直接执行
     - 触及边界   → 列出受影响的队友，先通知/讨论再执行
     - 改动 L0    → 必须向用户报告完整影响面，即使不触及边界
5. 给结论：直接改 / 需先通知队友 / 需先讨论方案
```

### 评估输出格式

每次评估必须输出如下格式的块：

```
【影响评估】
改动目标: <文件:函数/区域>
所属层次: L?
下游影响: <模块列表，无则写"无"]
触及边界: 是/否（若是，列出边界名称和受影响队友）
结论: <可直接改 / 需先通知XXX / 需先讨论>
```

### 常见场景速查

| 场景 | 结论 |
|---|---|
| 改 `prompts.py` 里的提示词文本 | L1 内部改动，不触及边界 → 可直接改 |
| 改 `graph.py` 内部节点逻辑但不改 `build_graph` 签名 | L1 内部改动 → 可直接改 |
| 改 `graph.py` 的 `build_graph()` 参数列表 | 触及边界 → 需通知王力涵（中间层调用方） |
| 改 `data_layer.py` 的 `DataStore` 公共方法 | L0 + 触及边界 → 需通知蒋状钊+王力涵 |
| 换数据库（SQLite → PostgreSQL） | L0 基础设施改动 → 需先讨论方案，影响 `data_layer.py`、`alert.py`、`data_service.py`、`graph.py` |
| 改 `config.py` 新增环境变量 | L0 + 触及边界（环境变量集合）→ 需通知所有部署者 |
| 改 `API_CONTRACT.md` 接口定义 | 触及边界 → 需通知所有相关方 |

---

## 5. 任务调度层级（栈 + 依附声明）

用栈区隔不同性质的工作线，每个任务额外声明**依附于谁**，确保新功能、重构、修复、测试不会互相吞没，且随时知道当前在做什么、为谁做。

### 5.1 任务类型定义

| 类型 | 标签 | 含义 | 约束 | 入栈前要求 |
|---|---|---|---|---|
| **主线** | `[主线]` | 推进项目功能前进 | 用户发起的顶层目标 | 无 |
| **重构** | `[重构]` | 改内部实现，不新增对外功能 | 不改对外接口；改接口属于重构+边界改动 | **必须先做 §4 影响评估** |
| **修复** | `[修复]` | 修正已有功能错误 | 不改对外接口；改接口的"修复"实质是重构 | **必须先做 §4 影响评估**，标注缺陷位置 |
| **测试** | `[测试]` | 验证已有功能正确性 | 依附被测对象，不独立成栈底（除非用户说"补测试"） | 标注被测模块 |
| **文档** | `[文档]` | 更新文档/契约 | 依附对应代码改动；小更新可旁路不压栈 | 标注关联文件 |

> **不新增类型**：保持 5 种，不扩展到 7~8 个。遇到感觉归不进去的任务，先重新界定边界归入现有类型（新增功能=主线，改实现不改行为=重构，修错误=修复，验证=测试，更新文档=文档）；实在归不进去的，先和用户讨论再决定，不要擅自新增。

### 5.2 依附关系矩阵

定义哪些任务类型可以依附于哪些类型。✅ = 允许，❌ = 禁止，独立 = 不依附任何任务。

| 子任务 ＼ 依附于 | 主线 | 重构 | 修复 | 测试 | 文档 | 独立 |
|---|---|---|---|---|---|---|
| **重构** | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| **修复** | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ |
| **测试** | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ |
| **文档** | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |

**关键规则**：
- **同类型不嵌套**：重构里不嵌套重构、修复里不嵌套修复（避免无限递归）。
- **修复可依附重构**：重构时引入了 bug，修复为重构服务。
- **修复可依附主线**：主线推进中发现已有 bug，修复为主线扫清障碍。
- **测试可依附修复**：修完 bug 写回归测试。
- **文档可依附任何任务**：任何代码改动后可能需要更新文档。

### 5.3 数据结构

栈中每个元素是一个三元组：

```
(类型, 目标, 依附对象)
```

- **类型**：主线 / 重构 / 修复 / 测试 / 文档
- **目标**：操作的模块/文件/函数，如 `data_layer`、`graph.py:123`、`/chat`
- **依附对象**：栈中另一个任务的引用，或 `null`（独立）

```
栈底 ──────────────────────────────────────── 栈顶
(主线, 中间层, null) → (重构, data_layer, 主线) → (测试, data_layer, 重构)
```

### 5.4 栈规则

1. **栈深度 ≤ 3**。需要更深时说明主线粒度太大，先与用户重新拆分，或开新对话（见规则 10）。
2. **入栈必须声明依附对象**，且依附关系必须符合 §5.2 矩阵。
3. **依附对象非栈顶时先弹栈**：若新任务依附于栈中某层但非栈顶，先 pop 到该层再 push。例如栈为 `[主线→重构→测试]`，发现 bug 依附于重构，则先 pop 测试，再 push 修复。
4. **重构/修复入栈前必须做 §4 影响评估**。触及 §3 接口边界或改动 L0 时，**先向用户报告并请求确认**。
5. **不能吞没上级**：重构/修复/测试完成后**必须弹栈回到依附对象**，不能做着做着偏离了原目标。
6. **文档可旁路**：小文档更新（改个 typo、补一行说明）不压栈，直接做；大改动（重写契约文档）压栈。
7. **栈状态可见**：用 `todowrite` 记录，格式 `[类型:目标]`，如 `[主线:中间层]`、`[重构:data_layer]`、`[修复:graph.py:123]`、`[测试:graph]`。
8. **一次只执行栈顶**：同一时刻只有栈顶 in_progress。
9. **弹栈时汇报**：pop 时向用户简述做了什么、改了哪些文件、是否触及边界。主线 pop 时需用户验收。
10. **复杂任务开新对话**：当满足以下任一条件时，建议开新对话而非在当前对话中硬撑：
    - 栈深度需要超过 3
    - 主线包含多个相互独立的大改动（如同时换数据库 + 加中间层 + 换前端框架）
    - 对话上下文已明显过长，AI 开始丢失早期信息

    **开新对话前必须做简要评估**（在当前对话中完成）：
    1. 当前任务栈状态：做到哪一步、栈中还剩什么
    2. 已完成改动：改了哪些文件、是否已提交、是否触及边界
    3. 未完成部分：下一步从哪接续、需要什么前提

    **开新对话时做交接**：
    - 在新对话首条消息中附上上述评估结果
    - 明确新对话的主线任务和起点
    - 可调用 `project-handoff` 技能辅助生成交接材料

### 5.5 栈操作示例

**示例 1：主线 → 重构 → 修复 → 测试（完整嵌套）**

```
用户: "实现中间层，但先把数据库换成 PostgreSQL"

→ push (主线, 中间层, null)
  → push (重构, data_layer, 主线)  SQLite → PostgreSQL
    → 影响评估：L0，触及边界 → 向用户确认 → 确认
    → 改 data_layer.py → 更新 alert/data_service 引用
    → push (修复, alert.py:78, 重构)  重构后 alert 连接参数断裂
      → 修引用
    → pop [修复]，汇报
    → push (测试, data_layer, 重构)  验证行为不变
    → pop [测试]，汇报
  → pop [重构]，汇报改动文件
  → push (主线步骤, /chat 骨架, 主线)
    → push (测试, /chat, 主线)
    → pop
  → pop
→ pop [主线]，请用户验收
```

**示例 2：纯修复 → 回归测试**

```
用户: "alert.py 管理后台登录校验有 bug"

→ push (修复, alert.py:登录校验, null)
  → 影响评估：L2，不触及边界（内部逻辑）
  → 修复
  → push (测试, alert, 修复)  回归测试
  → pop
→ pop [修复]，请用户验收
```

**示例 3：主线推进中发现无关已有 bug**

```
用户: "实现中间层"

→ push (主线, 中间层, null)
  → 开发 /chat 时发现 graph.py 已有 bug
  → push (修复, graph.py:456, 主线)  bug 阻碍主线推进
    → 修
    → push (测试, graph, 修复)
    → pop
  → pop [修复]，汇报
  → 继续主线...
→ pop [主线]
```

**示例 4：依附对象非栈顶，先弹栈再压**

```
当前栈: [主线 → 重构 → 测试]
  → 发现 bug 依附于重构（重构引入的），而非测试
  → pop [测试]  ← 先弹掉测试
  → push (修复, data_layer:120, 重构)
  → 修完
  → pop [修复]
  → push (测试, data_layer, 重构)  ← 重新压回测试
```

---

## 6. 代码规范

### 设计标准（总则）
- **方向对齐企业级，幅度按项目规模**：技术选型和设计方案对齐企业级通用做法，不按"大创/玩具级"标准自研简化变体；但项目规模小（约 10 人同时在线），落地时按实际情况合理裁剪，不引入企业级才需要的重型设施。
- **判断准则**：
  - 去掉某项只是"不够规范"但功能仍可用 → 按项目规模可以不做（如连接池、Protocol 抽象层、完整可观测性栈）。
  - 去掉某项会留安全隐患或维护陷阱 → 必须做（如鉴权用 JWT 双 token 而非裸奔、密码哈希而非明文）。
- **已落地的取舍示例**：
  - 鉴权：JWT 双 token（企业级标准），不引入 OAuth2/OIDC（项目小不需要）。
  - 代码结构：src layout（企业级），不抽 Protocol/抽象基类（YAGNI）。
  - 日志：structlog 结构化（企业级），不部署完整可观测性栈（项目小不需要）。
  - 数据库：PostgreSQL + psycopg v3（企业级），不用连接池（10 人并发远未达到门槛）。

### 通用
- Python ≥ 3.11，包管理用 `uv`（`uv sync` / `uv run`），不使用 conda/pip 直装。
- 包入口为 `src/xinqing/`（src layout），导入用相对导入 `from .module import X`，兼容 `from module import X` 回退。
- 文件编码 UTF-8。
- 不提交密钥、`.env`、数据库文件、模型权重（见 `.gitignore`）。

### 命名
- Python 文件/变量/函数：`snake_case`。
- 环境变量：`UPPER_SNAKE_CASE`，前缀按服务分组（`ALERT_*`、`DATA_*`、`DEEPSEEK_*`）。
- 数据库表/字段：`snake_case`，表名复数（`conversations`、`messages`）。

### 测试
- 测试目录：`tests/`，镜像 `src/xinqing/` 结构（如 `tests/xinqing/test_graph.py`）。
- 改动 L0/L1 模块时必须同时写/更新对应测试。
- 测试框架：pytest，配置在 `pyproject.toml` `[tool.pytest.ini_options]`（`testpaths=tests`、`pythonpath=src`）。
- 跑测试：`uv run pytest`。

### 代码质量（lint / 格式化 / 类型检查）
- 工具配置集中在 `pyproject.toml`（`[tool.ruff]`/`[tool.mypy]`），`.editorconfig` 统一编辑器，`.pre-commit-config.yaml` 提交前钩子。
- **提交前必跑**（或装 pre-commit 自动跑）：
  - lint + 自动修：`uv run ruff check --fix src tests`
  - 格式化：`uv run ruff format src tests`
  - 类型检查：`uv run mypy src/xinqing`（宽松配置，`ignore_missing_imports=true`，后续逐步收紧）
- dev 依赖：`uv sync --group dev` 装 ruff/mypy/pytest/pre-commit。
- pre-commit 启用：`uv run pre-commit install`（每人本地执行一次）。

### 日志与异常处理
- **统一用 structlog**：`logger = structlog.get_logger("xinqing.模块名")`。
- **集中配置**：`setup_logging()` 定义在 `logging_config.py`，只在入口模块（`app.py`/`alert.py`/`data_service.py`）启动时调用一次。被 import 的模块（`graph.py`/`data_layer.py` 等）**不要调** `setup_logging()`，只 `get_logger`。
- **except 块不静默吞**，按影响分级：
  - `logger.error` — 危机路径失败（告警没发出、风险评估异常）
  - `logger.warning` — 核心功能降级（数据库挂、检索挂、LLM 超时）
  - `logger.info` — 非关键辅助失败（动画、节假日 API）
- **结构化字段**：关键路径带 `user_id`/`conversation_id` 等上下文，普通日志不强制。
- **隐私约束**：告警日志只记元数据（`alert_id`/`user_id`/`risk_level`/`urgency`），**不记用户对话内容**。
- `ingest.py` 等 CLI 脚本可用 `print`，不强制接入。

### 提交
- 提交信息用简短单行中文或英文。
- 不在沙箱内执行 `git push`，由用户在本地终端推送。
- 每次提交前确认 `git status` 无意外文件（数据库、模型权重等）。

### 文档同步
- 改动接口边界后必须同步更新 `docs/API_CONTRACT.md`。
- 改动公共函数签名后必须同步更新 `README.md` 中的调用示例。

---

## 7. 当前项目状态快照

> 本节记录当前进度，每次大改动后更新。详细进度以 git 历史为准。

| 模块 | 状态 | 负责人 |
|---|---|---|
| 智能体工作流（workflow/ + common/config.py） | ✅ 代码完成，待端到端验证 | 蒋状钊 |
| 告警服务（data_service/alert.py） | ✅ 完成 | 王力涵 |
| 数据层（common/data_layer.py） | ✅ 完成 | 王力涵 |
| 数据 API（data_service/app.py） | ✅ 完成 | 王力涵 |
| Live2D 测试面板（web/live2d_demo/） | ✅ 完成 | 蒋状钊 |
| 部署脚本（scripts/） | ✅ 完成 | 王力涵 |
| 接口契约文档 | ✅ 完成 | 共同 |
| 目录结构重构（按服务分包） | ✅ 完成 | 王力涵 |
| 多模态中间层（middleware/） | ❌ 未开始 | 王力涵 |
| 正式 Web 前端 | ❌ 未开始 | 袁群 |
| 测试代码（tests/） | ❌ 未开始 | 共同 |
| 知识库索引构建 | ❌ 未跑通 | 蒋状钊 |

### 下一步（按 API_CONTRACT.md §9 联调顺序）
1. 蒋状钊：跑通 `ingest.py` 建 Chroma 索引 + 端到端测试三条分支
2. 王力涵：实现中间层骨架（`POST /chat` 文本透传 + `POST /avatar/command` 缓存 + `GET /health`）
3. 袁群：实现前端骨架（文本输入 + 文字回复展示）
4. 三方联调文字链路 → 接入 ASR/TTS → 接入立绘 → 危机路径联调