"""Kubernetes optimizer package (legacy module name retained for compatibility)."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent

__all__ = ["PACKAGE_ROOT"]
