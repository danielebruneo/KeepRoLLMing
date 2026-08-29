"""Route capability inheritance contract."""

from keeprollming.routing.router import resolve_inherited_route
from keeprollming.types import Route


def test_capabilities_inherit_and_empty_list_clears_parent() -> None:
    base = Route(
        name="base/local", pattern="base/local", capabilities=["chat", "tools"]
    )
    inherited = Route(name="chat/main", pattern="chat/main", extends="base/local")
    cleared = Route(
        name="chat/plain", pattern="chat/plain", extends="base/local", capabilities=[]
    )
    routes = {route.name: route for route in (base, inherited, cleared)}

    assert resolve_inherited_route(inherited, routes).capabilities == ["chat", "tools"]
    assert resolve_inherited_route(cleared, routes).capabilities == []
