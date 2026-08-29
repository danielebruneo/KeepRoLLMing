from keeprollming.observability.events_upstream import emit_transport_configured
from keeprollming.upstream import (
    configure_http_transport,
    get_http_transport_settings,
    make_request_timeout,
)


def test_route_deadline_and_pool_timeout_are_independent() -> None:
    configure_http_transport({"pool_timeout": 2.5, "connect_timeout": 7})
    timeout = make_request_timeout(45)

    assert timeout.read == 45
    assert timeout.write == 45
    assert timeout.pool == 2.5
    assert timeout.connect == 7


def test_effective_transport_policy_is_safe_copy() -> None:
    configure_http_transport({"max_connections": 3, "pool_timeout": 1.5})

    observed = get_http_transport_settings()
    observed["max_connections"] = 999

    assert get_http_transport_settings() == {
        "max_connections": 3,
        "max_keepalive_connections": 20,
        "keepalive_expiry": 30.0,
        "pool_timeout": 1.5,
        "connect_timeout": 60.0,
    }


def test_transport_configuration_event_contains_effective_policy() -> None:
    # Keep this test independent of xdist worker assignment and lifecycle
    # tests that configure the process-wide shared transport differently.
    configure_http_transport({"max_connections": 3, "pool_timeout": 1.5})
    captured = []

    class Dispatcher:
        def emit(self, event):
            captured.append(event)

    emit_transport_configured(
        get_http_transport_settings(),
        dispatcher=Dispatcher(),
    )

    assert captured[0].type == "execution.upstream.transport_configured"
    assert captured[0].data["max_connections"] == 3
    assert captured[0].data["pool_timeout"] == 1.5
