"""Request-local httpcore lifecycle traces, without bodies or credentials."""

from __future__ import annotations

import time
from typing import Any

from ..observability.events_upstream import emit_upstream_event


class UpstreamLifecycle:
    """Observe actual transport milestones, not backend-internal prefill state.

    Sent means handed to the local transport, not acknowledged/consumed by the
    peer. Reused connections need no TCP-connect event. Mock transports may not
    implement trace callbacks; explicit start/headers still provide boundaries.
    """

    def __init__(self, req_id: str, dispatcher: Any = None) -> None:
        self.req_id = req_id
        self.dispatcher = dispatcher
        self.started = time.monotonic()
        self.phase = None

    def mark(self, phase: str) -> None:
        if self.phase == phase:
            return
        self.phase = phase
        emit_upstream_event(
            self.req_id,
            "execution.upstream.phase",
            level="DEBUG",
            dispatcher=self.dispatcher,
            phase=phase,
            elapsed_ms=round((time.monotonic() - self.started) * 1000, 1),
        )

    async def trace(self, name: str, info: dict[str, Any]) -> None:
        # Do not persist info: it can include sockets, SSL objects and headers.
        if name.endswith("connect_tcp.started"):
            self.mark("connecting")
        elif name.endswith(("send_request_headers.started", "send_request_body.started")):
            self.mark("sending")
        elif name.endswith("send_request_body.complete"):
            self.mark("waiting_response_headers")
        elif name.endswith("receive_response_headers.complete"):
            self.mark("response_headers")
        elif name.endswith(".failed"):
            emit_upstream_event(
                self.req_id,
                "execution.upstream.transport_failed",
                level="BASIC",
                dispatcher=self.dispatcher,
                phase=self.phase,
                elapsed_ms=round((time.monotonic() - self.started) * 1000, 1),
                error_type=type(info.get("exception")).__name__,
            )
