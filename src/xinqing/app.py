"""LangGraph CLI/Studio entry point.

The default graph uses DeepSeek API models from config.py. In deployment,
construct the graph with real retrievers and alert/avatar endpoints.
"""

try:
    from .graph import build_graph
    from .ingest import load_retrievers
except ImportError:
    from graph import build_graph
    from ingest import load_retrievers


try:
    # Load the four persisted collections for normal application runs. Set
    # XINQING_DISABLE_RAG=1 when only graph topology inspection is needed.
    retrievers = {} if __import__("os").getenv("XINQING_DISABLE_RAG") == "1" else load_retrievers()
except Exception as exc:  # keep Studio/topology available when RAG is absent
    print(f"RAG 加载失败，使用空检索器: {exc}")
    retrievers = {}


graph = build_graph(retrievers=retrievers)
