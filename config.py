"""Central configuration for the LangGraph workflow.

Fill in ``DEEPSEEK_API_KEY`` for local use, or set the same value through the
``DEEPSEEK_API_KEY`` environment variable in deployment. Never commit a real
key to source control.
"""

from __future__ import annotations

import os
from typing import Any, Dict


# The requested global key placeholder. You may fill it here for local
# experiments; an environment variable takes precedence in deployment.
DEEPSEEK_API_KEY = ""
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", DEEPSEEK_API_KEY)
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# ``deepseek-chat`` remains a compatibility alias, but the current API model
# name is ``deepseek-v4-flash``. Override this if your account uses another
# model identifier.
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_TEMPERATURE = float(os.getenv("DEEPSEEK_TEMPERATURE", "0.7"))

# LangGraph sends crisis alerts to this service. The service implementation is
# in alert.py and can later route the same payload to email, a webhook, etc.
ALERT_HTTP_URL = os.getenv("ALERT_HTTP_URL", "http://127.0.0.1:5000/alert")
ALERT_HTTP_TIMEOUT = float(os.getenv("ALERT_HTTP_TIMEOUT", "10"))


def build_deepseek_models() -> Dict[str, Any]:
    """Create the DeepSeek models used by all workflow LLM nodes.

    The import is lazy so the rest of the package can still be inspected or
    compiled before optional runtime dependencies are installed. An empty key
    returns an empty mapping; ``graph.py`` then uses its deterministic
    development fallbacks.
    """

    if not DEEPSEEK_API_KEY:
        return {}

    try:
        from langchain_deepseek import ChatDeepSeek
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError(
            "已填写 DEEPSEEK_API_KEY，但未安装 langchain-deepseek；请运行 pip install -r requirements.txt"
        ) from exc

    def make(temperature: float) -> Any:
        return ChatDeepSeek(
            model=DEEPSEEK_MODEL,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            temperature=temperature,
        )

    # Keep a little randomness for language classification/translation, while
    # using deterministic settings for assessments and machine-readable action
    # commands.
    return {
        "intent_model": make(DEEPSEEK_TEMPERATURE),
        "emotion_translation_model": make(DEEPSEEK_TEMPERATURE),
        "emotion_label_model": make(DEEPSEEK_TEMPERATURE),
        "assessment_model": make(0.2),
        "crisis_context_model": make(0.2),
        "risk_assessment_model": make(0.1),
    "response_model": make(DEEPSEEK_TEMPERATURE),
    "assessment_summary_model": make(DEEPSEEK_TEMPERATURE),
    "crisis_response_model": make(0.2),
    "avatar_action_model": make(0.0),
    }
