"""Adapters: all extension-specific logic. An adapter module provides:

NAME, VERSION, OBSERVE_R (R hooks), PROFILE (toml), EXPECT_KEYS,
components(engine, observations, expect, canvas_pt) -> (components, assigned candidate paint indices),
component_id(i, comp), describe_component(comp), match(ref, cand) -> (pairs, missing, unexpected),
CHECKS {name: fn(ref, cand, P, add, expect) -> None, or False when not applicable}.
The core fails any event drawn by the port's own artists that is not assigned (DESIGN.md §5, "Ownership").
"""

from importlib import import_module


def load(name: str):
    try:
        return import_module(f"plotparity.adapters.{name}.adapter")
    except ModuleNotFoundError as exc:
        raise ValueError(f"unknown adapter {name!r}") from exc
