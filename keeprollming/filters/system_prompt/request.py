"""
System Prompt Filter - Adds or overrides the system prompt in every request.

Depending on config:
- If no system message exists: inserts one with the configured prompt
- If system message exists and override=False: prepends the prompt to existing
- If system message exists and override=True: replaces the system prompt entirely

Always triggers (no conditional logic like lazy detection).
"""

import copy
from typing import Any, Dict, List, Optional

from keeprollming.orchestrator.filter import (
    Filter,
    FilterConfig,
    FilterExecutionContext,
    Request,
)
from keeprollming.logger import log
from keeprollming.orchestrator.filters.events import (
    emit_system_prompt_inserted,
    emit_system_prompt_overridden,
    emit_system_prompt_prepended,
)


class SystemPromptFilter(Filter):
    """Inject or override the system prompt in every request.

    Priority: 10 (runs first, before TLS at 25 and nudge at 50).

    ``supports_streaming = False`` — this filter only modifies the *request*
    (Phase 1).  It does not need to inspect or alter individual SSE chunks
    in Phase 2.  Disabling it here prevents it from emitting content chunks
    before other filters (e.g. ModelNudge) get a chance to buffer them.
    ``supports_streaming`` controls whether the filter participates in the
    streaming chunk loop (Phase 2).  Setting it to ``False`` is correct for
    filters that only modify the request/response payload, not the stream.
    """

    supports_streaming = False
    _default_name = "system_prompt"
    priority = 10

    def __init__(self, config=None, **kwargs):
        if config is None:
            config = {}
        if isinstance(config, dict):
            enabled = config.get("enabled", kwargs.get("enabled", True))
            self._prompt = config.get("prompt", kwargs.get("prompt", ""))
            self._override = config.get("override", kwargs.get("override", False))
        else:
            enabled = config
            self._prompt = kwargs.get("prompt", "")
            self._override = kwargs.get("override", False)

        filter_config = FilterConfig(enabled=enabled, name="system_prompt")
        super().__init__(filter_config)

    async def process_request(
        self,
        request: Request,
        context: FilterExecutionContext,
    ) -> Request:
        """Insert or modify the system prompt in the request messages."""
        if not self.is_enabled or not self._prompt:
            return request

        req_id = self._resolve_req_id(context)
        messages = request.messages

        # Find existing system message
        system_idx = None
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                system_idx = i
                break

        if system_idx is None:
            # No system message — insert one at the beginning
            log(
                "INFO",
                "system_prompt_inserted",
                req_id=req_id,
                prompt_preview=self._prompt[:80],
                override=False,
            )
            emit_system_prompt_inserted(context, self._prompt[:80])
            messages.insert(0, {"role": "system", "content": self._prompt})
        elif self._override:
            # Override existing system prompt entirely
            log(
                "INFO",
                "system_prompt_overridden",
                req_id=req_id,
                prompt_preview=self._prompt[:80],
                override=True,
                old_length=len(messages[system_idx].get("content", "")),
            )
            emit_system_prompt_overridden(context, self._prompt[:80], len(messages[system_idx].get("content", "")))
            messages[system_idx] = {"role": "system", "content": self._prompt}
        else:
            # Prepend to existing system prompt
            existing = messages[system_idx].get("content", "")
            log(
                "INFO",
                "system_prompt_prepended",
                req_id=req_id,
                prompt_preview=self._prompt[:80],
                override=False,
                old_length=len(existing),
            )
            emit_system_prompt_prepended(context, self._prompt[:80], len(existing))
            messages[system_idx] = {
                "role": "system",
                "content": self._prompt + "\n\n" + existing,
            }

        return request

    async def process_response(self, response, context):
        """Pass through — this filter only modifies requests."""
        return response

    def reset(self) -> None:
        pass
