"""Tests for JSON store utilities."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from workflow_orchestrator.store import (
    PathResolutionError,
    ensure_container,
    resolve_path,
    write_path,
    write_with_metadata,
)


class StoreUtilityTests(unittest.TestCase):
    def test_resolve_path_success(self) -> None:
        data = {"optimization_flow": {"subjective": {"scores": {"total": 0.8}}}}
        value = resolve_path(data, "optimization_flow.subjective.scores.total")
        self.assertEqual(value, 0.8)

    def test_resolve_path_missing(self) -> None:
        data = {"optimization_flow": {}}
        with self.assertRaises(PathResolutionError):
            resolve_path(data, "optimization_flow.subjective")

    def test_ensure_container_creates_chain(self) -> None:
        data: dict[str, object] = {}
        container, key = ensure_container(data, "optimization_flow.results.subjective")
        self.assertIs(container, data["optimization_flow"]["results"])
        self.assertEqual(key, "subjective")

    def test_write_path_sets_value(self) -> None:
        data: dict[str, object] = {}
        write_path(data, "optimization_flow.subjective_evaluation_1", {"scores": {}})
        self.assertIn("optimization_flow", data)
        self.assertIn("subjective_evaluation_1", data["optimization_flow"])

    def test_write_with_metadata_dict(self) -> None:
        data: dict[str, object] = {}
        ts = datetime(2024, 4, 15, 12, 0, tzinfo=timezone.utc)
        provenance = {"instruction_key": "instruction"}
        write_with_metadata(
            data,
            "optimization_flow.subjective_evaluation_1",
            {"scores": {"total": 0.8}},
            created_at=ts,
            provenance=provenance,
        )
        stored = resolve_path(data, "optimization_flow.subjective_evaluation_1")
        self.assertEqual(stored["created_at"], ts.isoformat())
        self.assertEqual(stored["instruction_key"], "instruction")
        self.assertEqual(stored["scores"]["total"], 0.8)

    def test_write_with_metadata_scalar(self) -> None:
        data: dict[str, object] = {}
        ts = datetime(2024, 4, 15, 12, 0, tzinfo=timezone.utc)
        write_with_metadata(data, "optimization_flow.summary.score", 0.9, created_at=ts)
        self.assertEqual(resolve_path(data, "optimization_flow.summary.score"), 0.9)
        metadata = resolve_path(data, "optimization_flow.summary.score_metadata")
        self.assertEqual(metadata["created_at"], ts.isoformat())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
