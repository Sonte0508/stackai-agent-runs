"""
Cost model for the fake runner.

Prices are illustrative (roughly in line with public per-1K-token pricing
for comparable model tiers as of early 2026) and live in one place so the
whole cost story - per-step, per-run, and aggregate analytics - is derived
from a single source of truth instead of being recomputed inconsistently
in different places.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_1k: float
    output_per_1k: float


# Fake model catalog used by the runner. Names are deliberately generic
# ("fast" / "standard" / "reasoning" tiers) rather than real model names,
# since this is a simulated runner, not a real inference call.
MODEL_CATALOG: dict[str, ModelPricing] = {
    "sim-fast": ModelPricing(input_per_1k=0.00025, output_per_1k=0.00125),
    "sim-standard": ModelPricing(input_per_1k=0.003, output_per_1k=0.015),
    "sim-reasoning": ModelPricing(input_per_1k=0.015, output_per_1k=0.075),
}

# Tool calls don't consume tokens but still cost something (compute, a hosted
# search API, etc). Flat per-call cost, in USD.
TOOL_CALL_COST_USD = 0.0008


def model_call_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    pricing = MODEL_CATALOG.get(model, MODEL_CATALOG["sim-standard"])
    cost = (tokens_in / 1000) * pricing.input_per_1k + (tokens_out / 1000) * pricing.output_per_1k
    return round(cost, 6)


def tool_call_cost() -> float:
    return TOOL_CALL_COST_USD
