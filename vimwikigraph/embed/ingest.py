from __future__ import annotations

import os
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

from .chunker import MarkdownChunker
from .parser import Chunk


COLLECTION_NAME = "notes"
_BATCH_SIZE = 64


def _open_client(cfg: dict) -> chromadb.PersistentClient:
    return chromadb.PersistentClient(path=cfg["paths"]["db_path"])


def _get_or_create_collection(cfg: dict):
    client = _open_client(cfg)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return client, collection


def _get_collection(cfg: dict):
    client = _open_client(cfg)
    collection = client.get_collection(name=COLLECTION_NAME)
    return client, collection


def _make_chunker(cfg: dict) -> MarkdownChunker:
    return MarkdownChunker(
        max_tokens=cfg["chunker"]["max_tokens"],
        overlap_tokens=cfg["chunker"]["overlap_tokens"],
        parent_context_tokens=cfg["chunker"]["parent_context_tokens"],
        min_chunk_tokens=cfg["chunker"]["min_chunk_tokens"],
    )


def _resolve_model(cfg: dict) -> str:
    """Return the local snapshot path when cached, otherwise the HF repo id.

    Passing a local directory to SentenceTransformer skips the Hub etag check
    that would otherwise trigger a network round-trip (and possible re-download)
    on every startup.
    """
    from huggingface_hub import snapshot_download
    try:
        return snapshot_download(cfg["embedding"]["model"], local_files_only=True)
    except Exception:
        # Not cached yet — let SentenceTransformer download it normally.
        return cfg["embedding"]["model"]


def _load_model(cfg: dict) -> SentenceTransformer:
    return SentenceTransformer(
        _resolve_model(cfg),
        device=cfg["embedding"]["device"],
    )


def _upsert_chunks(collection, model: SentenceTransformer, chunks: list[Chunk]) -> None:
    """Embed and upsert chunks in batches."""
    for i in range(0, len(chunks), _BATCH_SIZE):
        batch = chunks[i : i + _BATCH_SIZE]
        texts = [c.text for c in batch]
        embeddings = model.encode(texts, normalize_embeddings=True).tolist()
        ids = [f"{c.source_file}::{c.chunk_index}" for c in batch]
        metadatas = [
            {
                "heading_path": c.heading_str,
                "heading_level": c.heading_level,
                "source_file": c.source_file,
                "chunk_index": c.chunk_index,
                "mtime": os.path.getmtime(c.source_file),
                **({"parent_text": c.parent_text} if c.parent_text else {}),
                **c.metadata,
            }
            for c in batch
        ]
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )


def build_index(cfg: dict) -> None:
    """Chunk all notes and upsert into ChromaDB. Safe to re-run (upserts by id)."""
    notes_path = Path(cfg["paths"]["notes_path"])
    md_files = sorted(
        f for pattern in ("*.md", "*.wiki") for f in notes_path.rglob(pattern)
    )
    print(f"Indexing {len(md_files)} file(s) from {notes_path} …")

    chunker = _make_chunker(cfg)
    model = _load_model(cfg)
    _, collection = _get_or_create_collection(cfg)

    all_chunks: list[Chunk] = []
    for f in md_files:
        all_chunks.extend(chunker.chunk_file(f))

    print(f"  {len(all_chunks)} chunks — embedding …")
    _upsert_chunks(collection, model, all_chunks)
    print("Done.")


def update_index(cfg: dict) -> None:
    """Re-chunk only files modified since last ingest."""
    notes_path = Path(cfg["paths"]["notes_path"])
    md_files = {
        str(f): f
        for pattern in ("*.md", "*.wiki")
        for f in notes_path.rglob(pattern)
    }

    try:
        _, collection = _get_collection(cfg)
    except Exception:
        # Collection doesn't exist yet — do a full build
        build_index(cfg)
        return

    # Gather stored mtime per source file
    results = collection.get(include=["metadatas"])
    stored_mtimes: dict[str, float] = {}
    for meta in results["metadatas"]:
        src = meta.get("source_file", "")
        if src and src not in stored_mtimes:
            stored_mtimes[src] = float(meta.get("mtime", 0.0))

    chunker = _make_chunker(cfg)
    model = _load_model(cfg)

    stale: list[Path] = []
    for src_str, path in md_files.items():
        if src_str not in stored_mtimes or path.stat().st_mtime > stored_mtimes[src_str]:
            stale.append(path)

    if not stale:
        print("Index is up to date.")
        return

    print(f"Re-indexing {len(stale)} changed file(s) …")
    for path in stale:
        collection.delete(where={"source_file": {"$eq": str(path)}})
        chunks = chunker.chunk_file(path)
        if chunks:
            _upsert_chunks(collection, model, chunks)
        print(f"  {path.name}: {len(chunks)} chunk(s)")
    print("Done.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build or update the embedding index.")
    parser.add_argument("--update", action="store_true", help="Incremental update only")
    args = parser.parse_args()

    from . import load_config
    cfg = load_config()
    if args.update:
        update_index(cfg)
    else:
        build_index(cfg)
