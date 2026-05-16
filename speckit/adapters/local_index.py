"""Local JSON-based BM25 spec index — no external services required."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from speckit.core.spec_parser import SpecFile

INDEX_FILE = ".speckit/index.json"
INDEX_VERSION = "1"

# BM25 tuning parameters
_K1 = 1.5
_B = 0.75


def _tokenize(text: str) -> list[str]:
    """Lower-case word tokeniser that keeps hyphens and underscores."""
    return re.findall(r"\b[a-z][a-z0-9_-]*\b", text.lower())


def _build_term_frequencies(spec: "SpecFile") -> dict[str, int]:
    """Build weighted TF map: frontmatter fields count more than body."""
    tokens = (
        _tokenize(spec.module) * 5
        + _tokenize(spec.title) * 3
        + _tokenize(spec.summary) * 2
        + _tokenize(" ".join(spec.affects)) * 3
        + _tokenize(" ".join(spec.source_files)) * 2
        + _tokenize(spec.content)
    )
    return dict(Counter(tokens))


class LocalIndex:
    """
    BM25-based local spec index.

    Persisted as JSON at .speckit/index.json. Zero external dependencies.
    """

    def __init__(self, project_root: Path, project_name: str):
        self.project_root = project_root
        self.project_name = project_name
        self.index_path = project_root / INDEX_FILE

    # ── build ────────────────────────────────────────────────────────────────

    def build(self, spec_files: list["SpecFile"]) -> int:
        """Index all spec files. Returns number of files indexed."""
        docs: list[dict] = []
        all_terms: set[str] = set()

        for spec in spec_files:
            tf = _build_term_frequencies(spec)
            all_terms.update(tf.keys())
            docs.append({
                "path": spec.path,
                "module": spec.module,
                "affects": spec.affects,
                "source_files": spec.source_files,
                "last_updated": spec.last_updated,
                "title": spec.title,
                "summary": spec.summary,
                "content": spec.content,
                "tf": tf,
                "doc_length": sum(tf.values()),
            })

        n = len(docs)
        avg_doc_length = sum(d["doc_length"] for d in docs) / n if n else 1.0

        # IDF: log((N - df + 0.5) / (df + 0.5) + 1)  — Robertson/Spärck Jones
        idf: dict[str, float] = {}
        for term in all_terms:
            df = sum(1 for d in docs if term in d["tf"])
            idf[term] = math.log((n - df + 0.5) / (df + 0.5) + 1)

        data = {
            "version": INDEX_VERSION,
            "project": self.project_name,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
            "avg_doc_length": avg_doc_length,
            "idf": idf,
            "docs": docs,
        }

        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return n

    # ── search ───────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """BM25 search over indexed specs. Returns top_k dicts sorted by score."""
        if not self.index_path.exists():
            return []

        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        query_terms = _tokenize(query)
        if not query_terms:
            return []

        idf: dict[str, float] = data["idf"]
        avg_dl: float = data["avg_doc_length"]

        scored: list[tuple[float, dict]] = []
        for doc in data["docs"]:
            tf_map: dict[str, int] = doc["tf"]
            dl: int = doc["doc_length"]
            score = 0.0
            for term in query_terms:
                tf = tf_map.get(term, 0)
                if tf == 0:
                    continue
                numerator = tf * (_K1 + 1)
                denominator = tf + _K1 * (1 - _B + _B * dl / avg_dl)
                score += idf.get(term, 0.0) * (numerator / denominator)
            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:top_k]]

    # ── helpers ──────────────────────────────────────────────────────────────

    def is_built(self) -> bool:
        return self.index_path.exists()

    def stats(self) -> dict:
        """Return basic stats about the current index."""
        if not self.index_path.exists():
            return {"built": False}
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        return {
            "built": True,
            "project": data.get("project"),
            "indexed_at": data.get("indexed_at"),
            "doc_count": len(data.get("docs", [])),
            "term_count": len(data.get("idf", {})),
        }
