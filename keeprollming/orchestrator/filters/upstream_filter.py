"""UpstreamFilter — makes the upstream LLM HTTP call.

Registered automatically via @register_filter.
The Pipeline uses this filter's process_request to make the upstream call
and its process_response to propagate the response.
"""

from ..filter import Filter, FilterExecutionContext, register_filter


@register_filter("upstream_call")
class UpstreamFilter(Filter):
    """The single filter that makes the upstream LLM call.

    Priority 1: runs first (lowest number) so other request filters
    can modify payload before the call, and response filters
    can process the response after.

    In practice, the Pipeline.run() method calls the upstream function
    directly between request and response phases. This filter exists
    for explicit registration and future composition scenarios.
    """

    priority = 1
    supports_streaming = True
    supports_non_streaming = True

    async def process_request(self, request, context):
        """No-op: the upstream call happens between request and response phases."""
        return request

    async def process_response(self, response, context):
        """No-op: response passes through to other filters."""
        return response
