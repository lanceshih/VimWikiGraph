from __future__ import annotations

from typing import Any

import chromadb
import numpy as np
from sentence_transformers import SentenceTransformer

from .ingest import COLLECTION_NAME


class Retriever:
    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._client = chromadb.PersistentClient(path=cfg["paths"]["db_path"])
        self._collection = self._client.get_collection(name=COLLECTION_NAME)
        from .ingest import _resolve_model
        self._model = SentenceTransformer(
            _resolve_model(cfg),
            device=cfg["embedding"]["device"],
        )
        # Cache: (collection_count, bm25_object, corpus)
        self._bm25_cache: tuple[int, Any, list[dict]] | None = None

    # ── Public ───────────────────────────────────────────────────────────────

    def query(
        self,
        text: str,
        top_k: int,
        filter_heading: str | None = None,
    ) -> list[dict]:
        """Return top_k results as dicts with keys: id, document, metadata, score."""
        where = _heading_where(filter_heading)
        dense = self._dense_query(text, top_k * 2, where)
        bm25 = self._bm25_query(text, top_k * 2, filter_heading)
        return _rrf_merge(dense, bm25, top_k)

    # ── Dense retrieval ───────────────────────────────────────────────────────

    def _dense_query(self, text: str, n: int, where: dict | None) -> list[dict]:
        total = self._collection.count()
        if total == 0:
            return []
        embedding = self._model.encode([text], normalize_embeddings=True).tolist()
        kwargs: dict = dict(
            query_embeddings=embedding,
            n_results=min(n, total),
            include=["documents", "metadatas", "distances"],
        )
        if where:
            kwargs["where"] = where
        results = self._collection.query(**kwargs)

        out = []
        for id_, doc, meta, dist in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            out.append(
                {
                    "id": id_,
                    "document": doc,
                    "metadata": meta,
                    "score": 1.0 - dist,  # cosine distance → similarity
                }
            )
        return out

    # ── BM25 retrieval ────────────────────────────────────────────────────────

    def _get_bm25(self) -> tuple[Any, list[dict]]:
        """Return a cached (BM25Okapi, corpus) built from the full collection."""
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            return None, []

        count = self._collection.count()
        if self._bm25_cache is not None and self._bm25_cache[0] == count:
            return self._bm25_cache[1], self._bm25_cache[2]

        results = self._collection.get(include=["documents", "metadatas"])
        corpus = [
            {"id": id_, "document": doc, "metadata": meta}
            for id_, doc, meta in zip(
                results["ids"], results["documents"], results["metadatas"]
            )
        ]
        if not corpus:
            return None, []

        bm25 = BM25Okapi([entry["document"].lower().split() for entry in corpus])
        self._bm25_cache = (count, bm25, corpus)
        return bm25, corpus

    def _bm25_query(
        self, text: str, n: int, filter_heading: str | None
    ) -> list[dict]:
        bm25, corpus = self._get_bm25()
        if bm25 is None or not corpus:
            return []

        scores = bm25.get_scores(text.lower().split())
        ranked = np.argsort(scores)[::-1]

        out = []
        for idx in ranked:
            if len(out) >= n:
                break
            if scores[idx] <= 0:
                break
            entry = corpus[idx]
            # Apply heading filter client-side
            if filter_heading:
                heading = entry["metadata"].get("heading_path", "")
                if filter_heading.lower() not in heading.lower():
                    continue
            out.append(
                {
                    "id": entry["id"],
                    "document": entry["document"],
                    "metadata": entry["metadata"],
                    "score": float(scores[idx]),
                }
            )
        return out


# ── Helpers ───────────────────────────────────────────────────────────────────

def _heading_where(filter_heading: str | None) -> dict | None:
    if not filter_heading:
        return None
    return {"heading_path": {"$contains": filter_heading}}


def _rrf_merge(
    dense: list[dict],
    bm25: list[dict],
    top_k: int,
    k: int = 60,
) -> list[dict]:
    """Reciprocal rank fusion of two ranked lists."""
    scores: dict[str, float] = {}
    index: dict[str, dict] = {}

    for rank, item in enumerate(dense):
        id_ = item["id"]
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank + 1)
        index[id_] = item

    for rank, item in enumerate(bm25):
        id_ = item["id"]
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank + 1)
        index[id_] = item

    ranked_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
    results = []
    for id_ in ranked_ids[:top_k]:
        entry = index[id_].copy()
        entry["score"] = scores[id_]
        results.append(entry)
    return results
