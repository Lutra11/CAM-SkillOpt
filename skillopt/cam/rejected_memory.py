"""Persistent rejected-edit memory for CAM-SkillOpt.

The default retriever is deliberately local and deterministic: it represents
failure-pattern text using character/word counts and cosine similarity.  A
production run may inject a semantic embedding function, while the fallback
keeps offline experiments reproducible and avoids pretending a remote model
was used when none is configured.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from math import sqrt
from pathlib import Path
from typing import Callable, Iterable


Vector = dict[str, float]
Embedder = Callable[[str], Vector]


@dataclass(frozen=True)
class RejectedEditMemoryItem:
    memory_id: str
    failure_pattern: str
    rejected_edit: str
    score_change: float
    benchmark: str
    task_type: str
    epoch: int
    step: int


@dataclass(frozen=True)
class RetrievedRejectedEdit:
    item: RejectedEditMemoryItem
    similarity: float
    retrieval_score: float


def lexical_embed(text: str) -> Vector:
    """Return a compact, deterministic sparse vector for English/Chinese text."""
    tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower())
    counts = Counter(tokens)
    norm = sqrt(sum(count * count for count in counts.values()))
    return {token: count / norm for token, count in counts.items()} if norm else {}


def cosine_similarity(left: Vector, right: Vector) -> float:
    """Compute cosine similarity for normalized or arbitrary sparse vectors."""
    if not left or not right:
        return 0.0
    dot = sum(weight * right.get(token, 0.0) for token, weight in left.items())
    left_norm = sqrt(sum(weight * weight for weight in left.values()))
    right_norm = sqrt(sum(weight * weight for weight in right.values()))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


class PersistentRejectedEditMemory:
    """JSON-backed cross-epoch negative-edit memory with deterministic ranking."""

    def __init__(self, path: str | Path, *, embedder: Embedder = lexical_embed) -> None:
        self.path = Path(path)
        self.embedder = embedder
        self.items: list[RejectedEditMemoryItem] = []
        self.load()

    def load(self) -> None:
        """Load prior records; missing memory is a valid cold start."""
        if not self.path.exists():
            self.items = []
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.items = [RejectedEditMemoryItem(**row) for row in payload.get("items", [])]

    def save(self) -> None:
        """Atomically persist records so an interrupted run cannot corrupt memory."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps({"schema_version": 1, "items": [asdict(item) for item in self.items]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_path, self.path)

    def add(self, item: RejectedEditMemoryItem) -> None:
        """Append a rejected update once, keyed by its stable memory identifier."""
        if any(existing.memory_id == item.memory_id for existing in self.items):
            return
        self.items.append(item)
        self.save()

    def add_rejected_step(
        self,
        *,
        failure_patterns: Iterable[str],
        rejected_edits: Iterable[str],
        score_change: float,
        benchmark: str,
        task_type: str,
        epoch: int,
        step: int,
    ) -> list[RejectedEditMemoryItem]:
        """Record each rejected edit against the observed failure evidence."""
        pattern_text = "; ".join(str(pattern).strip() for pattern in failure_patterns if str(pattern).strip())
        stored: list[RejectedEditMemoryItem] = []
        for index, edit in enumerate(rejected_edits):
            edit_text = str(edit).strip()
            if not edit_text:
                continue
            item = RejectedEditMemoryItem(
                memory_id=f"{benchmark}:e{epoch}:s{step}:r{index}",
                failure_pattern=pattern_text,
                rejected_edit=edit_text,
                score_change=float(score_change),
                benchmark=benchmark,
                task_type=task_type,
                epoch=int(epoch),
                step=int(step),
            )
            self.add(item)
            stored.append(item)
        return stored

    def retrieve(self, failure_pattern: str, *, top_k: int = 3) -> list[RetrievedRejectedEdit]:
        """Rank historical failures by similarity times observed damage magnitude."""
        if top_k < 1:
            return []
        query = self.embedder(failure_pattern)
        retrieved = []
        for item in self.items:
            similarity = cosine_similarity(query, self.embedder(item.failure_pattern))
            retrieved.append(
                RetrievedRejectedEdit(
                    item=item,
                    similarity=similarity,
                    retrieval_score=similarity * abs(item.score_change),
                )
            )
        return sorted(
            retrieved,
            key=lambda result: (result.retrieval_score, result.similarity, result.item.memory_id),
            reverse=True,
        )[:top_k]
