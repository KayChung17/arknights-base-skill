#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reproducibility metadata for solver runs.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical_json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _git_commit(root: Path) -> str | None:
    try:
        value = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        ).strip()
        return value or None
    except (OSError, subprocess.SubprocessError):
        return None


def find_repository_root(start: str | Path | None = None) -> Path:
    current = Path(start or __file__).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "skills").exists():
            return candidate
    return current


def build_manifest(
    *,
    run_type: str,
    command: list[str] | None = None,
    repository_root: str | Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(repository_root) if repository_root else find_repository_root()
    return {
        "manifest_schema_version": 1,
        "run_type": run_type,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "repository": {
            "version": _package_version("arknights-base-skill") or _read_project_version(root),
            "git_commit": _git_commit(root),
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "scipy": _package_version("scipy"),
            "openpyxl": _package_version("openpyxl"),
            "pid": os.getpid(),
        },
        "command": command if command is not None else list(sys.argv),
        "extra": extra or {},
    }


def _read_project_version(root: Path) -> str | None:
    path = root / "pyproject.toml"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version") and "=" in line:
            return line.split("=", 1)[1].strip().strip('"\'')
    return None
