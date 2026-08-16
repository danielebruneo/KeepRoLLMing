"""Regression coverage for example-route inheritance without a stream mode switch."""

from __future__ import annotations

import os

import yaml

from keeprollming.config import load_user_routes
from keeprollming.routing import resolve_inherited_route


def test_example_filtered_route_uses_canonical_filter_mapping():
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config.example.yaml")
    with open(config_path, encoding="utf-8") as source:
        routes = load_user_routes(yaml.safe_load(source))
    by_name = {route.name: route for route in routes}

    resolved = resolve_inherited_route(by_name["code/assistant"], by_name)
    assert resolved.filters is not None
    assert set(resolved.filters) == {"system_prompt", "model_nudge"}
