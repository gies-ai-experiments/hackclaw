"""Solution knowledge base with embedding-based similarity search."""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


@dataclass
class SolutionEntry:
    """A resolved ticket's problem + solution with its embedding."""

    ticket_id: str
    problem: str
    solution: str
    embedding: list[float] = field(default_factory=list)
    resolved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SolutionStore:
    """JSON-backed store for resolved ticket solutions with embeddings."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: list[SolutionEntry] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._entries = [SolutionEntry(**e) for e in data]
            except Exception as e:
                logger.warning("Failed to load solutions from {}: {}", self._path, e)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([asdict(e) for e in self._entries], indent=2),
            encoding="utf-8",
        )

    def add(
        self,
        *,
        ticket_id: str,
        problem: str,
        solution: str,
        embedding: list[float],
    ) -> SolutionEntry:
        """Add a new solution entry and persist to disk."""
        entry = SolutionEntry(
            ticket_id=ticket_id,
            problem=problem,
            solution=solution,
            embedding=embedding,
        )
        self._entries.append(entry)
        self._save()
        return entry

    def all(self) -> list[SolutionEntry]:
        """Return all stored solution entries."""
        return list(self._entries)

    def find_similar(
        self, embedding: list[float], threshold: float = 0.75
    ) -> list[tuple[SolutionEntry, float]]:
        """Return entries with cosine similarity above threshold, sorted by score descending."""
        results = []
        for entry in self._entries:
            if not entry.embedding:
                continue
            score = cosine_similarity(embedding, entry.embedding)
            if score >= threshold:
                results.append((entry, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results


async def generate_embedding(text: str) -> list[float]:
    """Generate an embedding via OpenAI text-embedding-3-small."""
    try:
        import openai

        client = openai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.warning("Failed to generate embedding: {}", e)
        return []
