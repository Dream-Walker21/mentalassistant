"""LangGraph version of the original Dify workflow.

The workflow deliberately keeps model, vector store, holiday API, and email
configuration outside the graph. Pass those dependencies to ``build_graph``
when deploying. The graph can still be exercised without them using the small
deterministic fallbacks in this module.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Literal, Mapping, Optional, Protocol, Sequence, Union
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import structlog

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, TypedDict

try:
    from .prompts import (
        ADJUSTED_DAY_SUPPORT_PROMPT,
        ASSESSMENT_PROMPT,
        ASSESSMENT_SUMMARY_PROMPT,
        AVATAR_ACTION_PROMPT,
        CRISIS_CONTEXT_PROMPT,
        CRISIS_RESPONSE_PROMPT,
        DAILY_SUPPORT_PROMPT,
        EMOTION_LABEL_PROMPT,
        EMOTION_TRANSLATION_PROMPT,
        HOLIDAY_SUPPORT_PROMPT,
        INTENT_CLASSIFIER_PROMPT,
        RISK_ASSESSMENT_PROMPT,
        WEEKEND_SUPPORT_PROMPT,
    )
    from .config import ALERT_API_TOKEN, ALERT_HTTP_TIMEOUT, ALERT_HTTP_URL, build_deepseek_models
    from .data_layer import DataStore
except ImportError:
    from prompts import (
        ADJUSTED_DAY_SUPPORT_PROMPT,
        ASSESSMENT_PROMPT,
        ASSESSMENT_SUMMARY_PROMPT,
        AVATAR_ACTION_PROMPT,
        CRISIS_CONTEXT_PROMPT,
        CRISIS_RESPONSE_PROMPT,
        DAILY_SUPPORT_PROMPT,
        EMOTION_LABEL_PROMPT,
        EMOTION_TRANSLATION_PROMPT,
        HOLIDAY_SUPPORT_PROMPT,
        INTENT_CLASSIFIER_PROMPT,
        RISK_ASSESSMENT_PROMPT,
        WEEKEND_SUPPORT_PROMPT,
    )
    from config import ALERT_API_TOKEN, ALERT_HTTP_TIMEOUT, ALERT_HTTP_URL, build_deepseek_models
    from data_layer import DataStore


logger = structlog.get_logger("xinqing.graph")


class Retriever(Protocol):
    """Minimal interface accepted by the graph for any LangChain retriever."""

    def invoke(self, query: str) -> Any: ...


class AppState(TypedDict, total=False):
    """State shared by every node.

    ``messages`` is append-only so a checkpointer can retain the full
    conversation. The other fields are intentionally plain JSON-compatible
    values, which makes the state easy to persist and inspect.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    query: str
    user_id: str
    conversation_id: str
    user_profile: Dict[str, Any]
    data_error: str
    conversation_history: str
    current_time: str
    intent: str
    holiday_info: Dict[str, Any]
    emotion: str
    retrieval_context: Dict[str, str]
    assessment: Dict[str, Any]
    risk_assessment: Dict[str, Any]
    alert_sent: bool
    alert_error: str
    response: str
    avatar_command: Dict[str, Any]
    avatar_command_sent: bool
    avatar_command_error: str
    _conversation_lock_key: str


ModelLike = Any
RetrieverMap = Mapping[str, Optional[Retriever]]
AlertSender = Callable[[Dict[str, Any]], Any]
HolidayFetcher = Callable[[str], Mapping[str, Any]]
AvatarCommandSender = Callable[[Dict[str, Any]], Any]


# LangGraph may run multiple requests in the same Python process. Keep turns
# for one conversation ordered while allowing different conversations to run
# concurrently. This is intentionally process-local; a multi-worker deploy
# should move the queue to Redis or another shared coordinator.
_conversation_locks: dict[str, threading.Lock] = {}
_conversation_locks_guard = threading.Lock()


def _conversation_lock(key: str) -> threading.Lock:
    with _conversation_locks_guard:
        lock = _conversation_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _conversation_locks[key] = lock
        return lock


def _render(template: str, values: Mapping[str, Any]) -> str:
    """Render neutral ``{{name}}`` placeholders without interpreting JSON braces."""

    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value if value is not None else ""))
    return rendered


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return " ".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
    return str(content)


def _history(state: AppState, limit: int = 25) -> str:
    messages = state.get("messages", [])[-limit:]
    return "\n".join(f"{getattr(m, 'type', 'message')}: {_message_text(m)}" for m in messages)


def _invoke(model: ModelLike, prompt: str, query: str) -> str:
    """Invoke a LangChain model or a simple callable, returning text."""

    if model is None:
        return ""
    messages = [SystemMessage(content=prompt), HumanMessage(content=query)]
    result = model.invoke(messages) if hasattr(model, "invoke") else model(messages)
    return _message_text(result).strip()


def _invoke_structured(model: ModelLike, prompt: str, query: str) -> Optional[Dict[str, Any]]:
    if model is None:
        return None
    try:
        structured_model = model.with_structured_output(dict) if hasattr(model, "with_structured_output") else model
        result = structured_model.invoke(
            [SystemMessage(content=prompt), HumanMessage(content=query)]
        ) if hasattr(structured_model, "invoke") else structured_model(prompt, query)
        if isinstance(result, Mapping):
            return dict(result)
        text = _message_text(result)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(match.group(0)) if match else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _retrieve(retriever: Optional[Retriever], query: str, max_chars: int = 8000) -> str:
    if retriever is None:
        return ""
    try:
        documents = retriever.invoke(query)
        if isinstance(documents, str):
            return documents[:max_chars]
        parts = []
        for document in documents or []:
            parts.append(str(getattr(document, "page_content", document)))
        return "\n\n".join(parts)[:max_chars]
    except Exception as exc:  # retrieval failure should not lose the conversation
        logger.warning("retrieve_failed", query=query[:80], error=str(exc))
        return f"知识库暂时不可用：{exc}"


def _post_json(
    url: str,
    payload: Mapping[str, Any],
    timeout: float = 5.0,
    headers: Optional[Mapping[str, str]] = None,
) -> Any:
    """Send one JSON command to the avatar/data-processing service."""

    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = Request(
        url,
        data=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else None


def _parse_avatar_command(raw: str, risk_assessment: Mapping[str, Any]) -> Dict[str, Any]:
    """Parse and validate the LLM action output against the engine whitelist."""

    allowed_actions = {"idle", "greet", "listen", "comfort", "think", "encourage", "alert", "goodbye"}
    allowed_expressions = {"neutral", "gentle_smile", "concerned", "calm", "serious"}
    allowed_gestures = {"none", "nod", "wave", "open_hands", "hand_on_heart", "point"}
    command: Dict[str, Any] = {}
    try:
        match = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, Mapping):
                command = dict(parsed)
    except (TypeError, ValueError, json.JSONDecodeError):
        command = {}

    high_risk = bool(
        risk_assessment.get("risk_flag")
        or risk_assessment.get("risk_level") in {"critical", "high"}
        or risk_assessment.get("urgency") == "高"
    )
    default = {
        "action": "alert" if high_risk else "listen",
        "expression": "serious" if high_risk else "calm",
        "gesture": "none" if high_risk else "nod",
        "intensity": 0.45 if high_risk else 0.3,
        "duration_ms": 1600 if high_risk else 1200,
    }
    action = command.get("action") if command.get("action") in allowed_actions else default["action"]
    expression = command.get("expression") if command.get("expression") in allowed_expressions else default["expression"]
    gesture = command.get("gesture") if command.get("gesture") in allowed_gestures else default["gesture"]
    if high_risk and expression in {"gentle_smile", "neutral"}:
        expression = "concerned"
    if high_risk and gesture == "wave":
        gesture = "none"
    try:
        intensity = min(1.0, max(0.0, float(command.get("intensity", default["intensity"]))))
    except (TypeError, ValueError):
        intensity = default["intensity"]
    try:
        duration_ms = min(10000, max(500, int(command.get("duration_ms", default["duration_ms"]))))
    except (TypeError, ValueError):
        duration_ms = default["duration_ms"]
    return {
        "version": 1,
        "action": action,
        "expression": expression,
        "gesture": gesture,
        "intensity": intensity,
        "duration_ms": duration_ms,
    }


def _keyword_intent(query: str) -> str:
    crisis_words = ("自杀", "自残", "轻生", "不想活", "结束生命", "伤害自己", "活不下去")
    assessment_words = ("心理状态", "情绪状态", "评估一下", "评估我的", "测试我的", "最近怎么样")
    if any(word in query for word in crisis_words):
        return "crisis"
    if any(word in query for word in assessment_words):
        return "condition_judgement"
    return "daily_support"


def _fallback_assessment(query: str) -> Dict[str, Any]:
    crisis = any(word in query for word in ("自杀", "自残", "轻生", "不想活", "结束生命"))
    anxious = any(word in query for word in ("焦虑", "紧张", "担心", "压力大"))
    depressed = any(word in query for word in ("抑郁", "绝望", "无意义", "撑不下去"))
    state = "抑郁" if depressed else "焦虑" if anxious else "其他"
    level = 5 if crisis else 4 if (anxious or depressed) else 2
    urgency = "高" if crisis else "中" if level >= 4 else "低"
    return {
        "emotional_state": state,
        "stress_level": level,
        "main_issues": ["其他"],
        "urgency": urgency,
        "support_needs": ["紧急干预" if crisis else "情感支持"],
        "risk_flag": crisis,
        "risk_level": "high" if crisis else "medium" if level >= 4 else "low",
        "risk_signals": ["疑似自伤/自杀表达"] if crisis else [],
        "confidence_level": 0.5,
    }


def _default_holiday(now: datetime) -> Dict[str, Any]:
    weekday = now.weekday()
    return {
        "date": now.strftime("%Y-%m-%d"),
        "weekday_name": ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")[weekday],
        "is_weekend": weekday >= 5,
        "is_holiday": False,
        "is_adjusted": False,
        "holiday_name": "",
    }


def build_graph(
    *,
    intent_model: ModelLike = None,
    emotion_translation_model: ModelLike = None,
    emotion_label_model: ModelLike = None,
    response_model: ModelLike = None,
    assessment_model: ModelLike = None,
    assessment_summary_model: ModelLike = None,
    crisis_context_model: ModelLike = None,
    risk_assessment_model: ModelLike = None,
    crisis_response_model: ModelLike = None,
    avatar_action_model: ModelLike = None,
    retrievers: Optional[RetrieverMap] = None,
    holiday_fetcher: Optional[HolidayFetcher] = None,
    alert_sender: Optional[AlertSender] = None,
    avatar_command_sender: Optional[AvatarCommandSender] = None,
    avatar_http_url: Optional[str] = None,
    alert_http_url: Optional[str] = None,
    data_store: Optional[DataStore] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
):
    """Build and compile the workflow.

    ``retrievers`` may contain ``emotion_support``, ``anxiety_scale`` and
    ``mental_health``. All dependencies are optional on purpose; configure
    them at the application boundary rather than in this file.
    """

    # Bind DeepSeek API defaults to every LLM node when its dependency is not
    # explicitly injected. Callers can still override any individual node.
    deepseek_models = build_deepseek_models()
    intent_model = intent_model or deepseek_models.get("intent_model")
    emotion_translation_model = emotion_translation_model or deepseek_models.get("emotion_translation_model")
    emotion_label_model = emotion_label_model or deepseek_models.get("emotion_label_model")
    assessment_model = assessment_model or deepseek_models.get("assessment_model")
    crisis_context_model = crisis_context_model or deepseek_models.get("crisis_context_model")
    risk_assessment_model = risk_assessment_model or deepseek_models.get("risk_assessment_model")
    response_model = response_model or deepseek_models.get("response_model")
    assessment_summary_model = assessment_summary_model or deepseek_models.get("assessment_summary_model")
    crisis_response_model = crisis_response_model or deepseek_models.get("crisis_response_model")
    avatar_action_model = avatar_action_model or deepseek_models.get("avatar_action_model")

    retrievers = dict(retrievers or {})
    alert_http_url = alert_http_url or ALERT_HTTP_URL
    data_store = data_store or DataStore()
    workflow = StateGraph(AppState)

    def prepare(state: AppState) -> Dict[str, Any]:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        user_id = state.get("user_id", "anonymous")
        fallback_conversation_id = str(state.get("conversation_id") or f"{user_id}-default")
        lock_key = f"{user_id}\x1f{fallback_conversation_id}"
        # Blocking acquire provides a FIFO-like wait queue for turns sharing
        # this conversation. Different conversation keys never block each other.
        _conversation_lock(lock_key).acquire()
        result = {
            "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "conversation_history": _history(state),
            "conversation_id": fallback_conversation_id,
            "_conversation_lock_key": lock_key,
            "user_profile": {"user_id": user_id, "preferred_name": "", "preferences": {}},
        }
        try:
            result["conversation_id"] = data_store.ensure_conversation(
                user_id, fallback_conversation_id, state.get("query", "")[:40]
            )
            result["user_profile"] = data_store.user_context(user_id)
            stored_messages = data_store.list_messages(user_id, result["conversation_id"], limit=24)
            history_lines = [f"{item['role']}: {item['content']}" for item in stored_messages]
            history_lines.append(f"user: {state.get('query', '')}")
            result["conversation_history"] = "\n".join(history_lines)
        except Exception as exc:
            # A data-service outage must not stop a mental-support response.
            logger.warning("load_context_failed", user_id=user_id, error=str(exc))
            result["data_error"] = str(exc)
        return result

    def classify_intent(state: AppState) -> Dict[str, Any]:
        query = state.get("query", "")
        prompt = _render(INTENT_CLASSIFIER_PROMPT, {"query": query})
        raw = _invoke(intent_model, prompt, query)
        normalized = raw.lower().replace("（", "(").replace("）", ")")
        if "crisis" in normalized or "危机" in raw:
            intent = "crisis"
        elif "condition" in normalized or "状态评估" in raw or "评估" in raw:
            intent = "condition_judgement"
        elif "daily" in normalized or "日常" in raw:
            intent = "daily_support"
        else:
            intent = _keyword_intent(query)
        return {"intent": intent}

    def daily_context(state: AppState) -> Dict[str, Any]:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        info = dict(_default_holiday(now))
        if holiday_fetcher is not None:
            try:
                info.update(dict(holiday_fetcher(now.strftime("%Y-%m-%d"))))
            except Exception as exc:
                logger.info("holiday_fetch_failed", error=str(exc))
                info["error"] = str(exc)
        return {"holiday_info": info}

    def daily_emotion(state: AppState) -> Dict[str, Any]:
        query = state.get("query", "")
        translated = _invoke(
            emotion_translation_model,
            _render(EMOTION_TRANSLATION_PROMPT, {"query": query}),
            query,
        ) or query
        retrieved = _retrieve(retrievers.get("emotion_support"), translated)
        anxiety_context = _retrieve(retrievers.get("anxiety_scale"), query)
        combined_context = "\n\n".join(
            part for part in (
                "[情绪支持知识库]\n" + retrieved if retrieved else "",
                "[焦虑量表知识库]\n" + anxiety_context if anxiety_context else "",
            ) if part
        )
        emotion = _invoke(
            emotion_label_model,
            _render(
                EMOTION_LABEL_PROMPT,
                {"query": query, "retrieval_context": combined_context},
            ),
            translated,
        ) or "待配置情绪识别模型"
        return {
            "emotion": emotion,
            "retrieval_context": {
                "emotion_support": retrieved,
                "anxiety_scale": anxiety_context,
                "combined_daily": combined_context,
            },
        }

    def daily_response(state: AppState) -> Dict[str, Any]:
        info = state.get("holiday_info", {})
        if info.get("is_holiday"):
            template = HOLIDAY_SUPPORT_PROMPT
        elif info.get("is_adjusted"):
            template = ADJUSTED_DAY_SUPPORT_PROMPT
        elif info.get("is_weekend"):
            template = WEEKEND_SUPPORT_PROMPT
        else:
            template = DAILY_SUPPORT_PROMPT
        values = {
            "current_time": state.get("current_time", ""),
            "weekday_name": info.get("weekday_name", ""),
            "holiday_name": info.get("holiday_name", ""),
            "intent": state.get("intent", "daily_support"),
            "query": state.get("query", ""),
            "emotion": state.get("emotion", ""),
            "retrieval_context": state.get("retrieval_context", {}).get("combined_daily", ""),
        }
        prompt = _render(template, values)
        history = state.get("conversation_history", "").strip()
        if history:
            prompt += "\n\n最近对话记录（请结合上下文自然回应，不要复述系统字段）：\n" + history
        profile = state.get("user_profile", {})
        if profile.get("preferred_name") or profile.get("preferences"):
            prompt += "\n用户偏好（仅在有助于回应时参考，不要提及系统保存的信息）：" + json.dumps(
                {"preferred_name": profile.get("preferred_name", ""), "preferences": profile.get("preferences", {})},
                ensure_ascii=False,
            )
        response = _invoke(response_model, prompt, state.get("query", ""))
        if not response:
            response = "我听见你现在的感受了。你可以先慢慢说说发生了什么，我们一起把眼前最困扰你的部分理清楚。"
        return {"response": response}

    def assess_state(state: AppState) -> Dict[str, Any]:
        history = state.get("conversation_history") or _history(state)
        prompt = _render(ASSESSMENT_PROMPT, {"conversation_history": history})
        assessment = _invoke_structured(assessment_model, prompt, state.get("query", ""))
        if not assessment:
            assessment = _fallback_assessment(state.get("query", ""))
        if "risk_level" not in assessment:
            assessment["risk_level"] = "high" if assessment.get("risk_flag") else "low"
        return {"assessment": assessment}

    def assessment_response(state: AppState) -> Dict[str, Any]:
        assessment = state.get("assessment", {})
        prompt = _render(
            ASSESSMENT_SUMMARY_PROMPT,
            {"assessment": json.dumps(assessment, ensure_ascii=False), "query": state.get("query", "")},
        )
        history = state.get("conversation_history", "").strip()
        if history:
            prompt += "\n\n最近对话记录：\n" + history
        response = _invoke(assessment_summary_model or response_model, prompt, state.get("query", ""))
        if not response:
            response = (
                f"根据当前对话的初步整理，你的主要情绪可能是“{assessment.get('emotional_state', '其他')}”，"
                f"压力等级约为 {assessment.get('stress_level', 1)}/5。这个结果不是医学诊断；如果困扰持续或影响生活，建议联系学校心理中心。"
            )
        return {"response": response}

    def crisis_context(state: AppState) -> Dict[str, Any]:
        query = state.get("query", "")
        mental_health_context = _retrieve(retrievers.get("mental_health"), query)
        first_aid_context = _retrieve(retrievers.get("crisis_first_aid"), query)
        retrieved = "\n\n".join(
            part for part in (
                "[精神健康诊疗参考]\n" + mental_health_context if mental_health_context else "",
                "[心理急救手册]\n" + first_aid_context if first_aid_context else "",
            ) if part
        )
        prompt = _render(
            CRISIS_CONTEXT_PROMPT,
            {
                "query": query,
                "conversation_history": state.get("conversation_history") or _history(state),
                "retrieval_context": retrieved,
            },
        )
        analysis = _invoke(crisis_context_model, prompt, query)
        return {
            "retrieval_context": {
                "mental_health": mental_health_context,
                "crisis_first_aid": first_aid_context,
                "crisis_combined": retrieved,
                "crisis_analysis": analysis,
            }
        }

    def crisis_assessment(state: AppState) -> Dict[str, Any]:
        retrieval_context = state.get("retrieval_context", {})
        context = "\n\n".join(
            part for part in (
                retrieval_context.get("crisis_combined", retrieval_context.get("mental_health", "")),
                "[危机知识分析]\n" + retrieval_context.get("crisis_analysis", "")
                if retrieval_context.get("crisis_analysis") else "",
            ) if part
        )
        prompt = _render(
            RISK_ASSESSMENT_PROMPT,
            {
                "query": state.get("query", ""),
                "conversation_history": state.get("conversation_history") or _history(state),
                "retrieval_context": context,
            },
        )
        assessment = _invoke_structured(risk_assessment_model, prompt, state.get("query", ""))
        if not assessment:
            assessment = _fallback_assessment(state.get("query", ""))
        return {"risk_assessment": assessment}

    def crisis_alert(state: AppState) -> Dict[str, Any]:
        assessment = state.get("risk_assessment", {})
        should_alert = bool(
            assessment.get("risk_flag")
            or assessment.get("risk_level") in {"critical", "high"}
            or assessment.get("urgency") == "高"
        )
        if not should_alert:
            return {"alert_sent": False}
        try:
            payload = {
                "user_id": state.get("user_id", "anonymous"),
                "query": state.get("query", ""),
                "risk_assessment": assessment,
                "timestamp": state.get("current_time", ""),
            }
            if alert_sender is not None:
                result = alert_sender(payload)
                sent = bool(result) if result is not None else True
            elif alert_http_url:
                # The receiver accepts {"alert_data": payload}; keeping this
                # boundary HTTP-based lets the delivery mechanism evolve
                # independently from the conversation graph.
                result = _post_json(
                    alert_http_url,
                    {"alert_data": payload},
                    timeout=ALERT_HTTP_TIMEOUT,
                    headers={"X-API-Key": ALERT_API_TOKEN} if ALERT_API_TOKEN else None,
                )
                sent = not isinstance(result, Mapping) or result.get("status") == "success"
            else:
                return {"alert_sent": False, "alert_error": "未配置告警接口"}
            return {"alert_sent": sent}
        except Exception as exc:
            logger.error(
                "alert_send_failed",
                user_id=state.get("user_id", "anonymous"),
                risk_level=assessment.get("risk_level", "unknown"),
                error=str(exc),
            )
            return {"alert_sent": False, "alert_error": str(exc)}

    def crisis_response(state: AppState) -> Dict[str, Any]:
        assessment = state.get("risk_assessment", {})
        prompt = _render(
            CRISIS_RESPONSE_PROMPT,
            {
                "intent": state.get("intent", "crisis"),
                "risk_assessment": json.dumps(assessment, ensure_ascii=False),
                "current_time": state.get("current_time", ""),
                "query": state.get("query", ""),
            },
        )
        history = state.get("conversation_history", "").strip()
        if history:
            prompt += "\n\n最近对话记录：\n" + history
        response = _invoke(crisis_response_model or response_model, prompt, state.get("query", ""))
        if not response:
            response = (
                "听起来你现在正承受着很强烈的痛苦，我很重视你此刻的安全。请先不要独处，"
                "联系身边可信任的人陪着你；如果你有立即伤害自己的危险，请马上联系当地急救服务。"
                "也可以拨打心理热线 025-58255200。"
            )
        return {"response": response}

    def avatar_action(state: AppState) -> Dict[str, Any]:
        """Choose a safe, whitelisted avatar command from the final response."""

        assessment = state.get("risk_assessment") or state.get("assessment") or {}
        prompt = _render(
            AVATAR_ACTION_PROMPT,
            {
                "query": state.get("query", ""),
                "response": state.get("response", ""),
                "intent": state.get("intent", "daily_support"),
                "risk_assessment": json.dumps(assessment, ensure_ascii=False),
            },
        )
        raw = _invoke(avatar_action_model, prompt, state.get("response", ""))
        return {"avatar_command": _parse_avatar_command(raw, assessment)}

    def avatar_dispatch(state: AppState) -> Dict[str, Any]:
        """Forward the command to an injected sender or an HTTP endpoint."""

        command = dict(state.get("avatar_command") or {})
        if not command:
            return {"avatar_command_sent": False}
        try:
            if avatar_command_sender is not None:
                result = avatar_command_sender(command)
                sent = result is not False
            elif avatar_http_url:
                _post_json(avatar_http_url, command)
                sent = True
            else:
                return {"avatar_command_sent": False}
            return {"avatar_command_sent": sent}
        except Exception as exc:
            # Avatar output is best-effort: a failed animation must not fail chat.
            logger.info("avatar_dispatch_failed", error=str(exc))
            return {"avatar_command_sent": False, "avatar_command_error": str(exc)}

    def finalize(state: AppState) -> Dict[str, Any]:
        user_id = state.get("user_id", "anonymous")
        conversation_id = state.get("conversation_id", "")
        try:
            data_store.add_message(user_id, conversation_id, "user", state.get("query", ""))
            data_store.add_message(
                user_id,
                conversation_id,
                "assistant",
                state.get("response", ""),
                {"intent": state.get("intent", ""), "avatar_command": state.get("avatar_command", {})},
            )
            if state.get("assessment"):
                data_store.save_assessment(user_id, "condition_judgement", state["assessment"], conversation_id)
            if state.get("risk_assessment"):
                data_store.save_assessment(user_id, "crisis", state["risk_assessment"], conversation_id)
        except Exception as exc:
            # Conversation storage must not prevent a user from receiving support.
            logger.warning("finalize_storage_failed", user_id=user_id, conversation_id=conversation_id, error=str(exc))
            return {"messages": [AIMessage(content=state.get("response", ""))], "data_error": str(exc)}
        finally:
            lock_key = state.get("_conversation_lock_key")
            if lock_key:
                _conversation_lock(lock_key).release()
        return {"messages": [AIMessage(content=state.get("response", ""))]}

    def route_intent(state: AppState) -> Literal["daily_support", "condition_judgement", "crisis"]:
        intent = state.get("intent", "daily_support")
        if intent not in {"daily_support", "condition_judgement", "crisis"}:
            return "daily_support"
        return intent  # type: ignore[return-value]

    workflow.add_node("prepare", prepare)
    workflow.add_node("classify_intent", classify_intent)
    workflow.add_node("daily_context", daily_context)
    workflow.add_node("daily_emotion", daily_emotion)
    workflow.add_node("daily_response", daily_response)
    workflow.add_node("assess_state", assess_state)
    workflow.add_node("assessment_response", assessment_response)
    workflow.add_node("crisis_context", crisis_context)
    workflow.add_node("crisis_assessment", crisis_assessment)
    workflow.add_node("crisis_alert", crisis_alert)
    workflow.add_node("crisis_response", crisis_response)
    workflow.add_node("avatar_action", avatar_action)
    workflow.add_node("avatar_dispatch", avatar_dispatch)
    workflow.add_node("finalize", finalize)

    workflow.add_edge(START, "prepare")
    workflow.add_edge("prepare", "classify_intent")
    workflow.add_conditional_edges(
        "classify_intent",
        route_intent,
        {
            "daily_support": "daily_context",
            "condition_judgement": "assess_state",
            "crisis": "crisis_context",
        },
    )
    workflow.add_edge("daily_context", "daily_emotion")
    workflow.add_edge("daily_emotion", "daily_response")
    workflow.add_edge("daily_response", "avatar_action")
    workflow.add_edge("assess_state", "assessment_response")
    workflow.add_edge("assessment_response", "avatar_action")
    workflow.add_edge("crisis_context", "crisis_assessment")
    workflow.add_edge("crisis_assessment", "crisis_alert")
    workflow.add_edge("crisis_alert", "crisis_response")
    workflow.add_edge("crisis_response", "avatar_action")
    workflow.add_edge("avatar_action", "avatar_dispatch")
    workflow.add_edge("avatar_dispatch", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile(checkpointer=checkpointer)


def invoke_graph(
    graph: Any,
    query: Union[str, Mapping[str, Any]],
    *,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> AppState:
    """Convenience wrapper for a single chat turn.

    When a checkpointer is configured, pass a stable ``thread_id`` to retain
    history across turns.

    ``query`` may be a plain string (legacy form) or an input object such as
    ``{"query": "最近很焦虑", "user_id": "student-001"}``. A keyword
    ``user_id`` takes precedence when both forms provide one.

    ``user_id`` is copied into the graph state and, on a crisis path, into the
    payload sent to the alert service.
    """

    if isinstance(query, Mapping):
        input_data = dict(query)
        actual_query = input_data.get("query", input_data.get("user_input", input_data.get("message", "")))
        input_user_id = input_data.get("user_id", input_data.get("userId", input_data.get("userID")))
    else:
        actual_query = query
        input_user_id = None

    if not isinstance(actual_query, str):
        actual_query = str(actual_query)
    actual_query = actual_query.strip()
    if not actual_query:
        raise ValueError("query 不能为空")
    resolved_user_id = str(user_id or input_user_id or "anonymous").strip() or "anonymous"

    run_config = dict(config or {})
    if thread_id:
        configurable = dict(run_config.get("configurable", {}))
        configurable["thread_id"] = thread_id
        run_config["configurable"] = configurable
    initial: AppState = {
        "query": actual_query,
        "user_id": resolved_user_id,
        "conversation_id": str(conversation_id or thread_id or f"{resolved_user_id}-default"),
        "messages": [HumanMessage(content=actual_query)],
    }
    return graph.invoke(initial, config=run_config or None)
