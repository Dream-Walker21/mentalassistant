"""Build local Chroma RAG collections from the original knowledge files.

The Dify dataset IDs are not portable. This script creates three explicit
collections that match the retriever names expected by graph.py:
``emotion_support``, ``anxiety_scale`` and ``mental_health``.
"""

from __future__ import annotations

import argparse
import gc
import os
import re
import shutil
from pathlib import Path
from typing import Dict, Iterable, List


# RAG ingestion uses PyTorch Sentence Transformers only. Prevent an unrelated
# TensorFlow/Keras installation in the user site-packages from being imported
# by Transformers on Windows.
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")


ROOT = Path(__file__).resolve().parent
KNOWLEDGE_DIR = ROOT / "knowledgefile"
# The standalone project uses an ASCII-only root path, so Chroma can safely
# persist beside the code without the Windows HNSW Unicode-path issue.
CHROMA_DIR = ROOT / "data" / "chroma"
MODELSCOPE_EMBEDDING_MODEL = "BAAI/bge-m3"
LOCAL_EMBEDDING_DIR = ROOT / "models" / "bge-m3"

# Sentence Transformers only needs the PyTorch encoder, tokenizer and module
# configuration below. The ModelScope repository also contains an optional
# ONNX export (including a second ~2 GB weight data file) which this RAG does not
# use, so it is intentionally excluded from the download.
REQUIRED_MODEL_FILES = [
    "config.json",
    "configuration.json",
    "config_sentence_transformers.json",
    "sentence_bert_config.json",
    "modules.json",
    "1_Pooling/config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "pytorch_model.bin",
]

SOURCE_MAP = {
    "emotion_support": KNOWLEDGE_DIR / "ESConv.md",
    "anxiety_scale": KNOWLEDGE_DIR / "1655286433fe468a.pdf",
    "mental_health": KNOWLEDGE_DIR / "mental.pdf",
    "crisis_first_aid": KNOWLEDGE_DIR / "WHO_心理急救_训练现场工作者的指导员手册.pdf",
}


HNSW_CONFIGURATION = {
    "hnsw": {
        "space": "cosine",
        "num_threads": 1,
        "batch_size": 64,
        "sync_threshold": 512,
    }
}


def _is_embedding_model_ready(model_dir: Path) -> bool:
    """Return whether a Sentence Transformers BGE-M3 snapshot is complete."""

    has_weights = any(
        (model_dir / filename).is_file()
        for filename in ("model.safetensors", "pytorch_model.bin")
    )
    return (
        has_weights
        and (model_dir / "config.json").is_file()
        and (model_dir / "modules.json").is_file()
        and (model_dir / "1_Pooling" / "config.json").is_file()
    )


def resolve_embedding_model(
    embedding_model: str = MODELSCOPE_EMBEDDING_MODEL,
    model_dir: Path = LOCAL_EMBEDDING_DIR,
) -> Path:
    """Resolve a local model directory, downloading BGE-M3 through ModelScope."""

    requested_path = Path(embedding_model).expanduser()
    if requested_path.is_dir():
        if not _is_embedding_model_ready(requested_path):
            raise RuntimeError(f"Embedding 模型目录不完整: {requested_path}")
        return requested_path

    if embedding_model != MODELSCOPE_EMBEDDING_MODEL:
        raise ValueError(
            "embedding_model 必须是本地模型目录，或当前支持的 ModelScope 模型 ID: "
            f"{MODELSCOPE_EMBEDDING_MODEL}"
        )

    if _is_embedding_model_ready(model_dir):
        return model_dir

    try:
        from modelscope import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "未安装 modelscope。请在运行 ingest.py 的同一 Python 环境中执行："
            "python -m pip install modelscope"
        ) from exc

    print(f"通过 ModelScope 下载 {embedding_model} 到: {model_dir}")
    downloaded_dir = Path(
        snapshot_download(
            embedding_model,
            local_dir=str(model_dir),
            allow_file_pattern=REQUIRED_MODEL_FILES,
        )
    )
    if not _is_embedding_model_ready(downloaded_dir):
        raise RuntimeError(f"ModelScope 下载后模型文件不完整: {downloaded_dir}")
    return downloaded_dir


def create_embeddings(
    embedding_model: str = MODELSCOPE_EMBEDDING_MODEL,
    model_dir: Path = LOCAL_EMBEDDING_DIR,
):
    """Create embeddings from the local ModelScope model directory."""

    import torch
    from langchain_huggingface import HuggingFaceEmbeddings

    local_model_path = resolve_embedding_model(embedding_model, model_dir)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Embedding 计算设备: {device}", flush=True)
    return HuggingFaceEmbeddings(
        model_name=str(local_model_path),
        model_kwargs={"device": device},
        encode_kwargs={"normalize_embeddings": True},
    )


def _load_source(path: Path, collection: str):
    from langchain_core.documents import Document

    if path.suffix.lower() == ".md":
        text = path.read_text(encoding="utf-8")
        # ESConv contains many independent conversations. Keep each dialogue
        # as a separate source document before applying the token-size split.
        sections = re.split(r"(?m)^##\s+对话\s+(\d+)\s*$", text)
        if len(sections) > 1:
            documents = []
            for index in range(1, len(sections), 2):
                dialogue_id = sections[index]
                content = sections[index + 1].strip()
                if content:
                    documents.append(
                        Document(
                            page_content=f"## 对话 {dialogue_id}\n\n{content}",
                            metadata={
                                "source": str(path),
                                "file_type": "markdown",
                                "dialogue_id": dialogue_id,
                                "collection": collection,
                            },
                        )
                    )
            if documents:
                return documents
        return [
            Document(
                page_content=text,
                metadata={"source": str(path), "file_type": "markdown", "collection": collection},
            )
        ]
    if path.suffix.lower() == ".pdf":
        from langchain_community.document_loaders import PyPDFLoader

        return PyPDFLoader(str(path)).load()
    raise ValueError(f"不支持的知识库文件类型: {path}")


def _split_documents(documents: Iterable, collection: str) -> List:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        add_start_index=True,
    )
    chunks = splitter.split_documents(list(documents))
    for chunk in chunks:
        chunk.metadata.update(
            {
                "collection": collection,
                "knowledge_version": "2026-08-29",
            }
        )
    return chunks


def build_collections(
    *,
    output_dir: Path = CHROMA_DIR,
    embedding_model: str = MODELSCOPE_EMBEDDING_MODEL,
    model_dir: Path = LOCAL_EMBEDDING_DIR,
    collections: Iterable[str] = SOURCE_MAP.keys(),
    reset: bool = False,
    batch_size: int = 64,
) -> Dict[str, int]:
    """Load, split and persist each source into its own Chroma collection."""

    import chromadb
    from chromadb.api.shared_system_client import SharedSystemClient
    from langchain_chroma import Chroma
    from tqdm.auto import tqdm

    if batch_size < 1:
        raise ValueError("batch_size 必须大于 0")

    if reset and output_dir.exists():
        resolved_output = output_dir.resolve()
        allowed_root = (ROOT / "data").resolve()
        if resolved_output != allowed_root and allowed_root not in resolved_output.parents:
            raise ValueError(f"--reset 只能清理 {allowed_root} 下的索引目录")
        print(f"清理旧的 Chroma 索引: {output_dir}")
        shutil.rmtree(output_dir)

    print("加载 Embedding 模型...", flush=True)
    embeddings = create_embeddings(embedding_model, model_dir)
    print("Embedding 模型加载完成，开始处理知识库。", flush=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(output_dir))
    counts: Dict[str, int] = {}
    for collection in collections:
        if collection not in SOURCE_MAP:
            raise KeyError(f"未知集合: {collection}")
        source = SOURCE_MAP[collection]
        if not source.exists():
            raise FileNotFoundError(source)

        print(f"[{collection}] 读取文件: {source.name}", flush=True)
        documents = _load_source(source, collection)
        print(f"[{collection}] 已读取 {len(documents)} 个文档，正在切分...", flush=True)
        chunks = _split_documents(documents, collection)
        print(f"[{collection}] 切分完成，共 {len(chunks)} 个片段，开始生成向量...", flush=True)

        vector_store = Chroma(
            collection_name=collection,
            embedding_function=embeddings,
            collection_configuration=HNSW_CONFIGURATION,
            client=client,
        )
        for start in tqdm(
            range(0, len(chunks), batch_size),
            desc=f"[{collection}] 写入 Chroma",
            unit="batch",
        ):
            vector_store.add_documents(chunks[start : start + batch_size])
        stored_count = vector_store._collection.count()
        if stored_count != len(chunks):
            raise RuntimeError(
                f"[{collection}] 写入校验失败：预期 {len(chunks)}，实际 {stored_count}"
            )
        counts[collection] = len(chunks)
        print(f"[{collection}] 完成。", flush=True)

    # PersistentClient caches systems within one process. Release it before
    # reopening so this verifies on-disk HNSW files, not cached in-memory data.
    del vector_store
    del client
    gc.collect()
    SharedSystemClient.clear_system_cache()

    verifier_client = chromadb.PersistentClient(path=str(output_dir))
    for collection, expected_count in counts.items():
        actual_count = verifier_client.get_collection(collection).count()
        if actual_count != expected_count:
            raise RuntimeError(
                f"[{collection}] 持久化校验失败：预期 {expected_count}，实际 {actual_count}"
            )
    print("Chroma 持久化校验通过。", flush=True)
    return counts


def load_retrievers(
    *,
    persist_directory: Path = CHROMA_DIR,
    embedding_model: str = MODELSCOPE_EMBEDDING_MODEL,
    model_dir: Path = LOCAL_EMBEDDING_DIR,
    k: int = 4,
) -> Dict[str, object]:
    """Open persisted Chroma collections in the shape expected by build_graph."""

    import chromadb
    from langchain_chroma import Chroma

    embeddings = create_embeddings(embedding_model, model_dir)
    client = chromadb.PersistentClient(path=str(persist_directory))
    return {
        name: Chroma(
            collection_name=name,
            embedding_function=embeddings,
            client=client,
        ).as_retriever(search_kwargs={"k": k})
        for name in SOURCE_MAP
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="将心晴助手知识文件导入 Chroma")
    parser.add_argument("--output", type=Path, default=CHROMA_DIR)
    parser.add_argument(
        "--embedding-model",
        default=MODELSCOPE_EMBEDDING_MODEL,
        help="ModelScope 模型 ID，或已下载的本地模型目录",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=LOCAL_EMBEDDING_DIR,
        help="ModelScope 下载 BGE-M3 时的本地保存目录",
    )
    parser.add_argument(
        "--collections",
        nargs="+",
        choices=list(SOURCE_MAP),
        default=list(SOURCE_MAP),
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="删除 --output 指向的旧 Chroma 索引后重新建库",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="每次送入 Embedding 和 Chroma 的片段数量，默认 64",
    )
    args = parser.parse_args()
    counts = build_collections(
        output_dir=args.output,
        embedding_model=args.embedding_model,
        model_dir=args.model_dir,
        collections=args.collections,
        reset=args.reset,
        batch_size=args.batch_size,
    )
    for collection, count in counts.items():
        print(f"{collection}: {count} chunks")


if __name__ == "__main__":
    main()
