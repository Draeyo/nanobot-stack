"""Adaptive model routing based on feedback quality signals.

Tracks per-model quality scores from user feedback and self-critique,
then adjusts routing preferences so higher-quality models get prioritized
for task types where they perform better.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
import time
from typing import Any

logger = logging.getLogger("rag-bridge.adaptive_router")

ADAPTIVE_ROUTING_ENABLED = os.getenv("ADAPTIVE_ROUTING_ENABLED", "true").lower() == "true"
STATE_DIR = pathlib.Path(os.getenv("STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))
SCORES_PATH = STATE_DIR / "adaptive_scores.json"
MIN_SAMPLES = int(os.getenv("ADAPTIVE_MIN_SAMPLES", "5"))
DECAY_FACTOR = float(os.getenv("ADAPTIVE_DECAY_FACTOR", "0.95"))


class AdaptiveRouter:
    """Tracks model quality and suggests routing adjustments."""

    def __init__(self):
        self._scores: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            if SCORES_PATH.exists():
                self._scores = json.loads(SCORES_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    def _save(self) -> None:
        try:
            SCORES_PATH.parent.mkdir(parents=True, exist_ok=True)
            SCORES_PATH.write_text(json.dumps(self._scores, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to persist adaptive scores: %s", exc)

    def _key(self, task_type: str, model: str) -> str:
        return f"{task_type}|{model}"

    def record_quality(self, task_type: str, model: str, score: float, source: str = "feedback") -> None:
        """Record a quality signal for a model on a task type.

        Args:
            task_type: The task type (e.g., 'retrieval_answer').
            model: The model name.
            score: Quality score 0.0 - 1.0.
            source: Signal source ('feedback', 'self_critique', 'latency').
        """
        if not ADAPTIVE_ROUTING_ENABLED:
            return
        k = self._key(task_type, model)
        with self._lock:
            if k not in self._scores:
                self._scores[k] = {
                    "task_type": task_type, "model": model,
                    "avg_score": 0.5, "samples": 0, "last_updated": 0.0,
                }
            entry = self._scores[k]
            # Exponential moving average
            n = entry["samples"]
            if n == 0:
                entry["avg_score"] = score
            else:
                alpha = max(0.1, 1.0 / (n + 1))
                entry["avg_score"] = entry["avg_score"] * (1 - alpha) + score * alpha
            entry["samples"] += 1
            entry["last_updated"] = time.time()
            # Apply time decay to old scores
            self._apply_decay(k)
            self._save()

    def _apply_decay(self, key: str) -> None:
        entry = self._scores.get(key)
        if not entry:
            return
        age_hours = (time.time() - entry["last_updated"]) / 3600
        if age_hours > 24:
            decay_rounds = int(age_hours / 24)
            entry["avg_score"] = 0.5 + (entry["avg_score"] - 0.5) * (DECAY_FACTOR ** decay_rounds)

    def get_model_ranking(self, task_type: str, candidates: list[str],
                          budget_pressure: float = 0.0) -> list[str]:
        """Rank candidate models by quality for a task type.

        Returns candidates sorted by quality (best first). Models without
        enough samples keep their original order.

        Args:
            budget_pressure: 0.0 = no pressure, 1.0 = extreme (prefer cheapest).
                When > 0.5, local models (ollama/*) get a quality score bonus.
                When > 0.8, local models are always preferred unless task is premium-only.
        """
        if not ADAPTIVE_ROUTING_ENABLED:
            return candidates

        premium_only_tasks = {"code_reasoning", "incident_triage"}

        scored = []
        unscored = []
        with self._lock:
            for model in candidates:
                k = self._key(task_type, model)
                entry = self._scores.get(k)
                if entry and entry["samples"] >= MIN_SAMPLES:
                    score = entry["avg_score"]
                    # Apply local model bonus under budget pressure
                    if budget_pressure > 0.5 and _is_local_model(model):
                        score += 0.2 * budget_pressure
                    scored.append((model, score))
                else:
                    unscored.append(model)

        # Under extreme budget pressure, force local models first
        if budget_pressure > 0.8 and task_type not in premium_only_tasks:
            local = [m for m in candidates if _is_local_model(m)]
            cloud = [m for m in candidates if not _is_local_model(m)]
            if local:
                return local + cloud

        scored.sort(key=lambda x: x[1], reverse=True)
        return [m for m, _ in scored] + unscored

    def should_prefer(self, task_type: str, model_a: str, model_b: str) -> str | None:
        """Return whichever model has a significantly higher quality score, or None."""
        if not ADAPTIVE_ROUTING_ENABLED:
            return None
        with self._lock:
            ka = self._key(task_type, model_a)
            kb = self._key(task_type, model_b)
            ea = self._scores.get(ka)
            eb = self._scores.get(kb)
            if not ea or ea["samples"] < MIN_SAMPLES:
                return None
            if not eb or eb["samples"] < MIN_SAMPLES:
                return None
            diff = ea["avg_score"] - eb["avg_score"]
            if diff > 0.15:
                return model_a
            elif diff < -0.15:
                return model_b
        return None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            entries = list(self._scores.values())
            return {
                "enabled": ADAPTIVE_ROUTING_ENABLED,
                "tracked_combinations": len(entries),
                "entries": sorted(entries, key=lambda x: x["avg_score"], reverse=True)[:20],
            }


def _is_local_model(model: str) -> bool:
    """Check if a model is a local (free) model."""
    return model.startswith("ollama/") or model.startswith("local_")


adaptive_router = AdaptiveRouter()
