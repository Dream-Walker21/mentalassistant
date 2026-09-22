"""TTS synthesis: edge-tts (lightweight, for testing) or GPT-SoVITS (HTTP, for production).

Returns the generated audio filename on success, or ``None`` on failure so the
caller can degrade gracefully (return text without audio).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
import uuid
from pathlib import Path

import structlog

logger = structlog.get_logger("xinqing.data_service.tts")

PROVIDER = os.getenv("TTS_PROVIDER", "edge").strip().lower()
VOICE = os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural").strip()
AUDIO_DIR = Path(os.getenv("XINQING_AUDIO_DIR", "data/audio")).resolve()
GPT_SOVITS_URL = os.getenv("GPT_SOVITS_BASE_URL", "").strip().rstrip("/")


def _audio_path() -> Path:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"resp-{int(time.time())}-{uuid.uuid4().hex[:8]}.mp3"
    return AUDIO_DIR / filename


def _synthesize_edge(text: str) -> str | None:
    try:
        import edge_tts

        path = _audio_path()

        async def _run() -> None:
            await edge_tts.Communicate(text, VOICE).save(str(path))

        asyncio.run(_run())
        return path.name
    except Exception as exc:
        logger.warning("tts_edge_failed", error=str(exc))
        return None


def _synthesize_gpt_sovits(text: str) -> str | None:
    if not GPT_SOVITS_URL:
        logger.warning("tts_gpt_sovits_not_configured")
        return None
    try:
        # TODO: confirm request/response format with a running GPT-SoVITS service.
        body = json.dumps({"text": text, "text_lang": "zh"}).encode("utf-8")
        req = urllib.request.Request(
            f"{GPT_SOVITS_URL}/tts",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        path = _audio_path()
        with urllib.request.urlopen(req, timeout=30) as resp:
            path.write_bytes(resp.read())
        return path.name
    except Exception as exc:
        logger.warning("tts_gpt_sovits_failed", error=str(exc))
        return None


def synthesize(text: str) -> str | None:
    """Synthesize speech for ``text``; return audio filename or ``None``."""
    if not text.strip():
        return None
    if PROVIDER == "gpt_sovits":
        return _synthesize_gpt_sovits(text)
    return _synthesize_edge(text)


def check_ready() -> bool:
    """Whether the configured TTS provider is usable."""
    if PROVIDER == "gpt_sovits":
        return bool(GPT_SOVITS_URL)
    try:
        import edge_tts  # noqa: F401

        return True
    except ImportError:
        return False
