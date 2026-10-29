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
from math import isfinite, sqrt
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
    kind: str = "rejected"
    # v3 records one optimizer update bundle, because a step-level paired
    # score change cannot identify which individual edit caused the change.
    attribution_scope: str = "per_edit"
    skill_before_hash: str | None = None
    skill_after_hash: str | None = None
    supporting_task_ids: tuple[str, ...] = ()
    support_count: int = 0


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
    """JSON-backed edit memory with a legacy retriever and evidence-aware v3."""

    def __init__(self, path: str | Path, *, embedder: Embedder = lexical_embed,
                 policy: str = "legacy", min_similarity: float | None = None,
                 age_half_life_steps: float | None = None,
                 half_life_steps: float | None = None) -> None:
        if policy not in {"legacy", "evidence_v3"}:
            raise ValueError("Memory policy must be legacy or evidence_v3")
        if age_half_life_steps is not None and half_life_steps is not None:
            raise ValueError("Specify only one memory half-life option")
        if half_life_steps is not None:
            age_half_life_steps = half_life_steps
        if min_similarity is not None and (not isfinite(min_similarity) or not 0 <= min_similarity <= 1):
            raise ValueError("Memory min_similarity must be finite and between 0 and 1")
        if age_half_life_steps is not None and (not isfinite(age_half_life_steps) or age_half_life_steps <= 0):
            raise ValueError("Memory age_half_life_steps must be finite and positive")
        self.path = Path(path)
        self.embedder = embedder
        self.policy = policy
        # Constructor-level v3 tuning must not silently alter legacy results.
        self.min_similarity = ((min_similarity if min_similarity is not None else 0.15)
                               if policy == "evidence_v3" else 0.0)
        self.age_half_life_steps = age_half_life_steps if policy == "evidence_v3" else None
        self.items: list[RejectedEditMemoryItem] = []
        self.load()

    def load(self) -> None:
        """Load prior records; missing memory is a valid cold start."""
        if not self.path.exists():
            self.items = []
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.items = []
        for row in payload.get("items", []):
            row = dict(row)
            # v3 serializes tuples as JSON arrays. Also tolerate early v3 rows
            # that did not yet persist an explicit support_count.
            row["supporting_task_ids"] = tuple(row.get("supporting_task_ids", ()))
            row.setdefault("support_count", len(row["supporting_task_ids"]))
            self.items.append(RejectedEditMemoryItem(**row))

    def save(self) -> None:
        """Atomically persist records so an interrupted run cannot corrupt memory."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps({"schema_version": 3 if self.policy == "evidence_v3" else 1,
                        "items": [asdict(item) for item in self.items]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_path, self.path)

    def add(self, item: RejectedEditMemoryItem) -> bool:
        """Append a rejected update once, keyed by its stable memory identifier."""
        if any(existing.memory_id == item.memory_id for existing in self.items):
            return False
        self.items.append(item)
        self.save()
        return True

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
        kind: str = "rejected",
        skill_before_hash: str | None = None,
        skill_after_hash: str | None = None,
        supporting_task_ids: Iterable[str] = (),
    ) -> list[RejectedEditMemoryItem]:
        """Record an edit against the observed failure evidence.

        `kind` is `rejected` for a scalar rejection or `inconclusive` for a
        paired-gate soft acceptance (v2): the edit was neither proven better
        nor proven worse, which is stored as uncertainty evidence, not a failure label.
        """
        allowed_kinds = {"rejected", "inconclusive"}
        if self.policy == "evidence_v3":
            allowed_kinds.add("accepted")
        if kind not in allowed_kinds:
            raise ValueError(f"Memory kind must be one of {', '.join(sorted(allowed_kinds))}")
        if not isfinite(float(score_change)):
            raise ValueError("Memory score_change must be finite")
        pattern_text = "; ".join(str(pattern).strip() for pattern in failure_patterns if str(pattern).strip())
        edits = [str(edit).strip() for edit in rejected_edits if str(edit).strip()]
        if self.policy == "evidence_v3":
            # One record per update: the selection delta belongs to the bundle,
            # and must not be presented as a causal score for any one edit.
            if not edits:
                return []
            ids = tuple(dict.fromkeys(str(value).strip() for value in supporting_task_ids if str(value).strip()))
            item = RejectedEditMemoryItem(
                memory_id=f"{benchmark}:e{epoch}:s{step}:bundle",
                failure_pattern=pattern_text, rejected_edit="\n".join(edits),
                score_change=float(score_change), benchmark=benchmark,
                task_type=task_type, epoch=int(epoch), step=int(step), kind=str(kind),
                attribution_scope="update_bundle", skill_before_hash=skill_before_hash,
                skill_after_hash=skill_after_hash, supporting_task_ids=ids,
                support_count=len(ids),
            )
            return [item] if self.add(item) else []

        stored: list[RejectedEditMemoryItem] = []
        for index, edit_text in enumerate(edits):
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
                kind=str(kind),
            )
            if self.add(item):
                stored.append(item)
        return stored

    def scoped_items(self, *, benchmark: str | None = None,
                     before_step: int | None = None,
                     through_epoch: int | None = None) -> list[RejectedEditMemoryItem]:
        """Select the same benchmark's strictly historical records, when scoped."""
        return [item for item in self.items
                if (benchmark is None or item.benchmark == benchmark)
                and (before_step is None or item.step < before_step)
                and (through_epoch is None or item.epoch <= through_epoch)]

    def retrieve(self, failure_pattern: str, *, top_k: int = 3,
                 benchmark: str | None = None, before_step: int | None = None,
                 through_epoch: int | None = None,
                 min_similarity: float | None = None,
                 task_type: str | None = None,
                 task_types: Iterable[str] | None = None) -> list[RetrievedRejectedEdit]:
        """Rank historical records using the active policy's evidence weight."""
        if top_k < 1:
            return []
        if min_similarity is None:
            min_similarity = self.min_similarity
        if not isfinite(min_similarity) or not 0 <= min_similarity <= 1:
            raise ValueError("Memory min_similarity must be finite and between 0 and 1")
        query = self.embedder(failure_pattern)
        if not query:
            return []
        retrieved = []
        scopes = set(task_types or ())
        if task_type is not None:
            scopes.add(task_type)
        for item in self.scoped_items(benchmark=benchmark, before_step=before_step,
                                      through_epoch=through_epoch):
            if self.policy == "evidence_v3" and scopes and not (
                    "overall" in scopes or "mixed" in scopes or item.task_type in {"overall", "mixed"}
                    or item.task_type in scopes):
                continue
            similarity = cosine_similarity(query, self.embedder(item.failure_pattern))
            # Returning top-k unrelated entries is not evidence of a memory hit.
            if not isfinite(similarity) or similarity <= min_similarity:
                continue
            if not isfinite(item.score_change):
                raise ValueError("Memory score_change must be finite")
            if self.policy == "evidence_v3":
                support = max(item.support_count, len(item.supporting_task_ids))
                confidence = support / (support + 2.0)
                # Keep uncertainty evidence retrievable even when its observed
                # signed delta is exactly zero. Sign is reported, never treated
                # as proof that an edit or bundle caused harm.
                retrieval_score = similarity * confidence
                if self.age_half_life_steps is not None and before_step is not None:
                    age = max(0, before_step - item.step)
                    retrieval_score *= 0.5 ** (age / self.age_half_life_steps)
            else:
                retrieval_score = similarity * abs(item.score_change)
            retrieved.append(
                RetrievedRejectedEdit(
                    item=item,
                    similarity=similarity,
                    retrieval_score=retrieval_score,
                )
            )
        # v2: soft-accepted (inconclusive) edits are weaker negative evidence than
        # true rejects, so they rank slightly lower at equal similarity.
        ranked = sorted(
            retrieved,
            key=lambda result: (
                result.retrieval_score * (1.0 if result.item.kind == "rejected" else 0.9),
                result.similarity,
                result.item.memory_id,
            ),
            reverse=True,
        )
        if self.policy == "evidence_v3":
            # Same bundle may be imported twice under different IDs. Retain the
            # strongest matching record once in a retrieval result.
            unique = []
            seen = set()
            for result in ranked:
                key = (result.item.task_type, result.item.rejected_edit.strip())
                if key in seen:
                    continue
                seen.add(key)
                unique.append(result)
            ranked = unique
        return ranked[:top_k]
