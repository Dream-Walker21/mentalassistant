"""LangGraph CLI/Studio entry point.

The default graph uses DeepSeek API models from config.py. In deployment,
construct the graph with real retrievers and alert/avatar endpoints.
"""

import structlog

from ..common.logging_config import setup_logging
from .graph import build_graph
from .ingest import load_retrievers

setup_logging()
logger = structlog.get_logger("xinqing.app")


try:
    # Load the four persisted collections for normal application runs. Set
    # XINQING_DISABLE_RAG=1 when only graph topology inspection is needed.
    retrievers = {} if __import__("os").getenv("XINQING_DISABLE_RAG") == "1" else load_retrievers()
except Exception as exc:  # keep Studio/topology available when RAG is absent
    logger.warning("rag_load_failed", error=str(exc))
    retrievers = {}


graph = build_graph(retrievers=retrievers)
