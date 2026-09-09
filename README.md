# 心晴助手 LangGraph 工作流

这是原 Dify 工作流的 LangGraph 版本。流程保留了原来的三类意图：

```text
输入 -> 意图分类
          ├─ daily_support -> 日期/节假日 -> 情绪识别与检索 -> 支持性回复
          ├─ condition_judgement -> 对话历史评估 -> 状态说明
          └─ crisis -> 危机知识检索 -> 结构化风险评估 -> 告警 -> 危机支持回复
                                      ↓
                         统一进入立绘动作选择 -> HTTP/函数发送
```

## 依赖和运行

```powershell
cd E:\work\mentaldemo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -U "langgraph-cli[inmem]"
```

配置仍然留在应用边界，不写进图代码。`build_graph()` 接受以下可选依赖：

默认情况下，所有 LLM 节点（包括三个用户回复节点）都会自动使用 `config.py` 中的 DeepSeek API：意图分类、情绪翻译、情绪标签、日常支持回复、状态评估、状态说明、危机知识分析、危机风险评估、危机支持回复、立绘动作决策。仍可在 `build_graph()` 中单独传入模型覆盖默认值。

- `intent_model`：意图分类模型；
- `emotion_translation_model`、`emotion_label_model`：情绪识别模型；
- `response_model`、`assessment_summary_model`、`crisis_response_model`：回复模型；
- `assessment_model`、`risk_assessment_model`：支持结构化输出的模型；
- `crisis_context_model`：危机知识检索后的分析模型；
- `retrievers`：`emotion_support`、`anxiety_scale`、`mental_health`、`crisis_first_aid` 四个检索器；
- `holiday_fetcher`：接收 `YYYY-MM-DD` 并返回日期信息的函数；
- `alert_sender`：接收告警字典并返回成功状态的函数；
- `alert_http_url`：告警接收接口，默认 `http://127.0.0.1:5000/alert`；
- `avatar_action_model`：根据最终回复选择立绘动作的独立模型；
- `avatar_command_sender`：接收动作 JSON 的函数；
- `avatar_http_url`：数据处理端 HTTP POST 地址；若同时配置 sender，优先使用 sender；
- `checkpointer`：例如 `MemorySaver` 或生产环境的数据库 Checkpointer。

在 [config.py](E:/work/mentaldemo/config.py) 中填写 `DEEPSEEK_API_KEY` 即可启用默认模型；也可以通过同名环境变量提供密钥。推荐环境变量方式，避免把密钥写入代码。

最小调用示例：

```python
# 原来的 Ollama 示例（暂时停用）：
# from langchain_ollama import ChatOllama
# model = ChatOllama(model="gemma3:12b", temperature=0.7)

from config import DEEPSEEK_API_KEY
from langgraph.checkpoint.memory import MemorySaver

from graph import build_graph, invoke_graph

graph = build_graph(
    avatar_http_url="http://127.0.0.1:8000/avatar/command",
    alert_http_url="http://127.0.0.1:5000/alert",
    checkpointer=MemorySaver(),
)
result = invoke_graph(
    graph,
    {"query": "最近有点焦虑，学习压力很大", "user_id": "demo-user"},
    thread_id="demo-user",
)
print(result["response"])
```

旧的调用方式仍兼容：`invoke_graph(graph, "问题", user_id="demo-user")`。如果两处都传入用户 ID，关键字参数优先。

模型、向量库、邮箱和 API 的具体配置故意没有写入此目录。知识库需要重新导入到 LangChain 兼容的 Vector Store，Dify 的 `dataset_id` 不能直接复用。

## 知识库迁移

原文件对应关系如下：

| 文件 | 集合名 | 用途 |
|---|---|---|
| `knowledgefile/ESConv.md` | `emotion_support` | 情绪支持对话示例 |
| `knowledgefile/1655286433fe468a.pdf` | `anxiety_scale` | 焦虑自评量表（SAS） |
| `knowledgefile/mental.pdf` | `mental_health` | 精神障碍诊疗规范参考 |
| `knowledgefile/WHO_心理急救_训练现场工作者的指导员手册.pdf` | `crisis_first_aid` | WHO 心理急救与现场支持 |

Dify 的 `dataset_id`、切分结果和索引不能直接导入。运行 [ingest.py](E:/work/mentaldemo/ingest.py) 会读取 Markdown/PDF，按中文标点切成带 metadata 的片段，并写入本地 Chroma 索引。Embedding 模型默认由 ModelScope 下载，不再访问 Hugging Face：

```powershell
conda activate pyenv0
cd E:\work\mentaldemo
pip install -r requirements.txt
python ingest.py
```

如果出现 `tf_keras` 或 `Keras 3` 错误，通常是 `pip` 把包装到了用户目录而不是 `pyenv0`。请在同一个环境中执行：

```powershell
conda activate pyenv0
$env:PYTHONNOUSERSITE = "1"
python -c "import sys; print(sys.executable)"
python -m pip install -r requirements.txt
python -s ingest.py
```

输出的 Python 路径必须是 `D:\anaconda\envs\pyenv0\python.exe`。`ingest.py` 已默认禁用不需要的 TensorFlow 后端；不要为这个 RAG 导入任务安装 `tf-keras`，除非你的其它项目确实需要 TensorFlow。

首次运行会通过 ModelScope 下载 `BAAI/bge-m3` 到 `E:\work\mentaldemo\models\bge-m3`；下载中断后再次执行同一条命令可继续。脚本只下载 Chroma 所需的 PyTorch、Tokenizer 和 Sentence Transformers 配置，不下载可选的 ONNX 推理权重。也可以自行指定模型保存目录：

```powershell
python -s ingest.py --model-dir "E:\models\bge-m3"
```

如果模型已经位于某个完整的本地目录，则传入该目录后不会再联网下载：

```powershell
python -s ingest.py --embedding-model "E:\models\bge-m3"
```

应用侧用 `load_retrievers()` 打开四个集合，再传入 `build_graph(retrievers=...)`。危机路径会同时查询 `mental_health` 和 `crisis_first_aid`。查询时 `k=4` 与原 Dify 工作流的 Top-K 保持一致。

Windows 下 Chroma 的 HNSW 后端不能可靠使用包含中文字符的持久化路径，因此默认索引保存到纯 ASCII 路径：

```text
C:\Users\27732\AppData\Local\XinQingAssistant\chroma
```

可在启动前通过环境变量指定另一个纯 ASCII 路径：

```powershell
$env:XINQING_RAG_DIR = "E:\XinQingAssistant\chroma"
```

如果导入过程中被中断，可使用 `--reset` 删除旧的、可再生的 Chroma 索引后重建：

```powershell
python -s ingest.py --reset
```

`--reset` 只会删除项目内 `data\chroma` 索引目录，不会删除 `knowledgefile` 中的原始资料或 `models` 中的 Embedding 模型。

导入时会显示每个集合的读取、切分和 Chroma 写入批次进度。默认每批处理 64 个片段；CPU 内存不足时可调低为 16 或 32：

```powershell
python -s ingest.py --reset --batch-size 32
```

脚本在结束前会清除当前进程的 Chroma client 缓存，再重新打开三个集合并校验记录数；只有看到 `Chroma 持久化校验通过。` 和三个 chunks 统计行，才表示索引可被后续 RAG 查询使用。

PDF 若是扫描图片而没有文本层，需要先 OCR；当前三个文件可以直接读取文本层。SAS 和诊疗规范应作为检索参考，不能让模型据此直接下医学诊断。

## 导入 LangGraph

LangGraph 不支持直接导入 Dify 的 `workflow.yml`；Dify YAML 是 Dify 私有格式，当前 [graph.py](E:/work/mentaldemo/graph.py) 是对应的 Python `StateGraph` 重实现。[langgraph.json](E:/work/mentaldemo/langgraph.json) 和 [app.py](E:/work/mentaldemo/app.py) 已提供 CLI/Studio 入口：

```powershell
conda activate pyenv0
cd E:\work\mentaldemo
langgraph dev
```

当前 LangGraph CLI 要求 Python 3.11 或更高版本；可先运行 `python --version` 检查。`langgraph dev` 会启动本地 Agent Server，默认地址通常是 `http://127.0.0.1:2024`，再用输出中的 Studio 地址打开图。

`app.py` 默认会加载 `data\chroma` 下的四个 Retriever 并构造完整工作流；如果只想查看图结构、暂时跳过 Embedding 模型加载，可设置 `XINQING_DISABLE_RAG=1`。

要手动构造真实服务，应使用：

```python
from graph import build_graph
from ingest import load_retrievers

retrievers = load_retrievers()
graph = build_graph(retrievers=retrievers, alert_http_url="http://127.0.0.1:5000/alert")
```

首次使用 Studio 时，导入的是 `langgraph.json` 指向的已编译 `graph` 对象，而不是原 Dify YAML。CLI/Studio 只负责运行和查看图，模型密钥、向量库和告警服务仍需在 `.env` 或部署环境中配置。

## 立绘动作接口

动作节点位于每条回复分支之后、`finalize` 之前。数据处理端需要提供一个 `POST` 接口，例如：

```json
{
  "version": 1,
  "action": "comfort",
  "expression": "calm",
  "gesture": "hand_on_heart",
  "intensity": 0.4,
  "duration_ms": 1600
}
```

`action`、`expression`、`gesture` 会经过白名单校验；危机状态会强制避免微笑和挥手。HTTP 发送失败只记录在 `avatar_command_error`，不会阻断文字回复。

## 告警服务

LangGraph 只向告警接口发送 JSON，不直接连接 SMTP。启动接收和发送服务：

```powershell
python E:\work\mentaldemo\alert.py
```

默认通道是邮件。请通过环境变量配置，不要把密码写入源代码：

```powershell
$env:ALERT_CHANNEL = "email"
$env:SMTP_USERNAME = "your@qq.com"
$env:SMTP_PASSWORD = "your-smtp-authorization-code"
$env:ALERT_RECIPIENTS = "counselor@example.com,center@example.com"
```

后续可以切换为日志或 webhook：

```powershell
$env:ALERT_CHANNEL = "log"
# 或
$env:ALERT_CHANNEL = "webhook"
$env:ALERT_WEBHOOK_URL = "https://example.com/alert"
```

服务接口：`POST /alert`，请求体可以是 `{"alert_data": {"user_id": "demo-user", ...}}`；健康检查为 `GET /health`。新增发送方式时，实现 `AlertNotifier.send()` 并在 `create_notifier()` 注册即可，不需要修改 LangGraph。

## 安全注意事项

危机流程只提供风险信号和支持建议，不应被当作医学诊断。生产环境应使用“模型评估 + 确定性规则复核 + 人工介入”，并对用户 ID、输入内容和告警日志进行脱敏、访问控制和有限期保存。不要把 API Key 或 SMTP 密码放在前端或源代码中。
