#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve structured base-skill effects with explicit stacking semantics."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import prod
from typing import Iterable


SUPPORTED_STACKING = {"add", "max", "replace", "multiply", "exclusive"}


@dataclass(frozen=True)
class EffectContribution:
    effect_key: str
    stacking: str
    value: float
    source: str
    priority: int = 0


def resolve_effects(contributions: Iterable[EffectContribution]) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Resolve contributions by effect key and return values plus provenance."""
    grouped: dict[str, list[EffectContribution]] = defaultdict(list)
    for contribution in contributions:
        if contribution.stacking not in SUPPORTED_STACKING:
            raise ValueError(f"unsupported stacking mode: {contribution.stacking}")
        grouped[contribution.effect_key].append(contribution)

    values: dict[str, float] = {}
    sources: dict[str, list[str]] = {}
    for effect_key, items in grouped.items():
        modes = {item.stacking for item in items}
        if len(modes) != 1:
            raise ValueError(f"mixed stacking modes for {effect_key}: {sorted(modes)}")
        mode = items[0].stacking
        if mode == "add":
            value = sum(item.value for item in items)
            winners = items
        elif mode == "max":
            value = max(item.value for item in items)
            winners = [item for item in items if abs(item.value - value) <= 1e-12]
        elif mode == "multiply":
            value = prod(item.value for item in items)
            winners = items
        else:
            winner = max(items, key=lambda item: (item.priority, item.value, item.source))
            value = winner.value
            winners = [winner]
        values[effect_key] = value
        sources[effect_key] = sorted({item.source for item in winners})
    return values, sources
