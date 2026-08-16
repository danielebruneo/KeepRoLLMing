"""Resolved-route filter validation contract."""

import pytest

from keeprollming.config import validate_resolved_route_filters
from keeprollming.types import Route


def test_startup_validation_checks_an_inherited_filter_configuration():
    parent = Route(
        name="parent",
        pattern="parent/*",
        model="test",
        filters={"not_a_filter": {"enabled": True}},
    )
    child = Route(name="child", pattern="child/*", model="test", extends="parent")

    with pytest.raises(ValueError, match="resolved route 'parent'.*not_a_filter"):
        validate_resolved_route_filters([parent, child])
