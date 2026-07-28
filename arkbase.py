#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repository-level launcher for the Chinese CLI."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    scripts = Path(__file__).resolve().parent / "skills" / "ark-base-schedule" / "scripts"
    sys.path.insert(0, str(scripts))
    runpy.run_path(str(scripts / "arkbase.py"), run_name="__main__")


if __name__ == "__main__":
    main()
