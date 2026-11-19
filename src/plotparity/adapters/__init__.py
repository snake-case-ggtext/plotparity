"""Adapters: all extension-specific logic. An adapter module provides:

NAME, VERSION, OBSERVE_R (R hooks), PROFILE (toml), EXPECT_KEYS,
components(engine, observations, expect) -> (components, unassigned_events),
component_id(i, comp), match(ref, cand) -> (pairs, missing, unexpected), CHECKS {name: fn}.
"""

from importlib import import_module


def load(name: str):
    try:
        return import_module(f"plotparity.adapters.{name}.adapter")
    except ModuleNotFoundError as exc:
        raise ValueError(f"unknown adapter {name!r}") from exc
