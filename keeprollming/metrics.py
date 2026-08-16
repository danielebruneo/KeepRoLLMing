"""Metrics module — cost/load/retry accounting.

**Ownership boundary:**
- `performance.py` = per-request performance data (TPS, TTFT, elapsed time,
  token counts for logging).
- `metrics.py` = cost/load/retry accounting (consumes ExecutionUsage directly
  from the pipeline after stream completion).

Token counts in this module are derived from `ExecutionUsage`, not tracked
independently. This module consumes `ExecutionUsage` directly from the
pipeline after stream completion.

**Key invariants:**
- I1: metrics.py consumes ExecutionUsage, not standard usage, for cost/load
  accounting (D-018, D-022).
- I2: performance.py continues to own per-request performance data (TPS,
  TTFT, elapsed time).
- I3: /metrics endpoint serves SystemMetrics from metrics.py.
- I4: ExecutionUsage fields exposed to metrics.py: upstream_attempts,
  usage_reported_attempts, recovery_count, retry_amplification_ratio,
  usage_complete.
- I5: Token counts in metrics.py are derived from ExecutionUsage, not tracked
  independently.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from collections import defaultdict, deque
from dataclasses import dataclass, asdict

from .streaming.accounting import ExecutionUsage


@dataclass
class ConversationMetrics:
    """Tracks detailed metrics for conversations.

    Token counts are derived from ExecutionUsage, not tracked independently.
    """
    conversation_id: str
    user_id: str
    model_used: str
    total_messages: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    summary_used: bool
    summary_tokens: int
    context_length: int
    elapsed_time_ms: float
    request_count: int
    first_message_length: int
    last_message_length: int
    avg_message_length: float
    summary_decision_reason: str
    upstream_attempts: int = 0
    usage_reported_attempts: int = 0
    recovery_count: int = 0
    retry_amplification_ratio: float = 0.0
    usage_complete: bool = False


@dataclass
class SystemMetrics:
    """Tracks overall system performance metrics.

    Aggregates ExecutionUsage-derived data across all conversations.
    """
    timestamp: float
    total_requests: int
    average_request_time_ms: float
    token_usage_by_model: Dict[str, int]
    summary_cache_hits: int
    summary_cache_misses: int
    summary_reuse_count: int
    max_conversation_length: int
    avg_conversation_length: float
    total_upstream_attempts: int = 0
    total_usage_reported_attempts: int = 0
    total_recovery_count: int = 0
    avg_retry_amplification_ratio: float = 0.0
    total_usage_complete: int = 0


class MetricsCollector:
    """Collects and aggregates performance metrics for the orchestrator.

    Consumes ExecutionUsage directly from the pipeline after stream completion.
    Token counts are derived from ExecutionUsage, not tracked independently.
    """

    def __init__(self):
        self.conversations: Dict[str, ConversationMetrics] = {}
        self.system_metrics = SystemMetrics(
            timestamp=time.time(),
            total_requests=0,
            average_request_time_ms=0.0,
            token_usage_by_model={},
            summary_cache_hits=0,
            summary_cache_misses=0,
            summary_reuse_count=0,
            max_conversation_length=0,
            avg_conversation_length=0.0,
            total_upstream_attempts=0,
            total_usage_reported_attempts=0,
            total_recovery_count=0,
            avg_retry_amplification_ratio=0.0,
            total_usage_complete=0
        )
        self.request_times: deque[float] = deque(maxlen=1000)
        self.conversation_lengths: deque[int] = deque(maxlen=1000)

    def record_request(self, conversation_id: str, user_id: str, model_used: str,
                      prompt_tokens: int, completion_tokens: int, total_tokens: int,
                      summary_used: bool, summary_tokens: int, context_length: int,
                      elapsed_time_ms: float, request_count: int, first_message_length: int,
                      last_message_length: int, avg_message_length: float, summary_decision_reason: str,
                      execution_usage: Optional[ExecutionUsage] = None):
        """Record metrics for a single conversation.

        Token counts are derived from ExecutionUsage if provided, otherwise
        use the explicit parameters for backward compatibility.
        """
        # Update system-level metrics
        self.system_metrics.total_requests += 1
        self.request_times.append(elapsed_time_ms)
        self.conversation_lengths.append(request_count)

        if len(self.request_times) > 0:
            self.system_metrics.average_request_time_ms = sum(self.request_times) / len(self.request_times)

        # Update token usage by model
        if model_used not in self.system_metrics.token_usage_by_model:
            self.system_metrics.token_usage_by_model[model_used] = 0
        self.system_metrics.token_usage_by_model[model_used] += total_tokens

        # Derive ExecutionUsage fields if available
        eu_upstream_attempts = 0
        eu_usage_reported_attempts = 0
        eu_recovery_count = 0
        eu_retry_amplification_ratio = 0.0
        eu_usage_complete = False
        eu_prompt_tokens = prompt_tokens
        eu_completion_tokens = completion_tokens
        eu_total_tokens = total_tokens

        if execution_usage is not None:
            eu_upstream_attempts = execution_usage.upstream_attempts
            eu_usage_reported_attempts = execution_usage.usage_reported_attempts
            eu_recovery_count = execution_usage.recovery_count
            eu_retry_amplification_ratio = execution_usage.retry_amplification_ratio
            eu_usage_complete = execution_usage.usage_complete
            # Cost/load metrics consume explicit cumulative upstream totals.
            if execution_usage.upstream_prompt_tokens > 0:
                eu_prompt_tokens = execution_usage.upstream_prompt_tokens
            if execution_usage.upstream_completion_tokens > 0:
                eu_completion_tokens = execution_usage.upstream_completion_tokens
            if execution_usage.upstream_total_tokens > 0:
                eu_total_tokens = execution_usage.upstream_total_tokens

        # Track conversation metrics
        self.conversations[conversation_id] = ConversationMetrics(
            conversation_id=conversation_id,
            user_id=user_id,
            model_used=model_used,
            total_messages=request_count,
            prompt_tokens=eu_prompt_tokens,
            completion_tokens=eu_completion_tokens,
            total_tokens=eu_total_tokens,
            summary_used=summary_used,
            summary_tokens=summary_tokens,
            context_length=context_length,
            elapsed_time_ms=elapsed_time_ms,
            request_count=request_count,
            first_message_length=first_message_length,
            last_message_length=last_message_length,
            avg_message_length=avg_message_length,
            summary_decision_reason=summary_decision_reason,
            upstream_attempts=eu_upstream_attempts,
            usage_reported_attempts=eu_usage_reported_attempts,
            recovery_count=eu_recovery_count,
            retry_amplification_ratio=eu_retry_amplification_ratio,
            usage_complete=eu_usage_complete
        )

        # Update system-level ExecutionUsage aggregates
        self.system_metrics.total_upstream_attempts += eu_upstream_attempts
        self.system_metrics.total_usage_reported_attempts += eu_usage_reported_attempts
        self.system_metrics.total_recovery_count += eu_recovery_count
        if eu_upstream_attempts > 0:
            self.system_metrics.avg_retry_amplification_ratio = (
                (self.system_metrics.avg_retry_amplification_ratio * (self.system_metrics.total_requests - 1) + eu_retry_amplification_ratio)
                / self.system_metrics.total_requests
            )
        if eu_usage_complete:
            self.system_metrics.total_usage_complete += 1

    def record_summary_cache_hit(self):
        """Record a cache hit for summary"""
        self.system_metrics.summary_cache_hits += 1

    def record_summary_cache_miss(self):
        """Record a cache miss for summary"""
        self.system_metrics.summary_cache_misses += 1

    def record_summary_reuse(self):
        """Record when a summary is reused/consolidated"""
        self.system_metrics.summary_reuse_count += 1

    def get_system_metrics(self) -> Dict[str, Any]:
        """Get system-wide metrics as dict"""

        # Update max and average conversation lengths
        if len(self.conversation_lengths) > 0:
            self.system_metrics.max_conversation_length = max(self.conversation_lengths)
            self.system_metrics.avg_conversation_length = sum(self.conversation_lengths) / len(self.conversation_lengths)

        return asdict(self.system_metrics)

    def get_conversation_metrics(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get metrics for a specific conversation"""
        if conversation_id in self.conversations:
            return asdict(self.conversations[conversation_id])
        return None

    def get_all_conversation_metrics(self) -> List[Dict[str, Any]]:
        """Get all recorded conversation metrics"""
        return [asdict(metrics) for metrics in self.conversations.values()]

    def reset_system_metrics(self):
        """Reset system-level metrics (for periodic reporting)"""
        self.system_metrics = SystemMetrics(
            timestamp=time.time(),
            total_requests=0,
            average_request_time_ms=0.0,
            token_usage_by_model={},
            summary_cache_hits=0,
            summary_cache_misses=0,
            summary_reuse_count=0,
            max_conversation_length=0,
            avg_conversation_length=0.0,
            total_upstream_attempts=0,
            total_usage_reported_attempts=0,
            total_recovery_count=0,
            avg_retry_amplification_ratio=0.0,
            total_usage_complete=0
        )

    def get_summary_statistics(self) -> Dict[str, Any]:
        """Get summary statistics of the system behavior"""
        stats = {
            "total_requests": self.system_metrics.total_requests,
            "average_request_time_ms": self.system_metrics.average_request_time_ms,
            "summary_cache_hits": self.system_metrics.summary_cache_hits,
            "summary_cache_misses": self.system_metrics.summary_cache_misses,
            "summary_reuse_count": self.system_metrics.summary_reuse_count,
            "max_conversation_length": self.system_metrics.max_conversation_length,
            "avg_conversation_length": self.system_metrics.avg_conversation_length,
        }

        # Add token usage breakdown
        stats["token_usage_by_model"] = {
            model: tokens for model, tokens in self.system_metrics.token_usage_by_model.items()
        }

        # Add ExecutionUsage aggregates
        stats["total_upstream_attempts"] = self.system_metrics.total_upstream_attempts
        stats["total_usage_reported_attempts"] = self.system_metrics.total_usage_reported_attempts
        stats["total_recovery_count"] = self.system_metrics.total_recovery_count
        stats["avg_retry_amplification_ratio"] = self.system_metrics.avg_retry_amplification_ratio
        stats["total_usage_complete"] = self.system_metrics.total_usage_complete

        return stats


# Global metrics collector instance
METRICS_COLLECTOR = MetricsCollector()

def record_conversation_metrics(conversation_id: str, user_id: str, model_used: str,
                               prompt_tokens: int, completion_tokens: int, total_tokens: int,
                               summary_used: bool, summary_tokens: int, context_length: int,
                               elapsed_time_ms: float, request_count: int, first_message_length: int,
                               last_message_length: int, avg_message_length: float, summary_decision_reason: str,
                               execution_usage: Optional[ExecutionUsage] = None):
    """Record metrics for a conversation - convenience function.

    Token counts are derived from ExecutionUsage if provided, otherwise
    use the explicit parameters for backward compatibility.
    """
    METRICS_COLLECTOR.record_request(
        conversation_id=conversation_id,
        user_id=user_id,
        model_used=model_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        summary_used=summary_used,
        summary_tokens=summary_tokens,
        context_length=context_length,
        elapsed_time_ms=elapsed_time_ms,
        request_count=request_count,
        first_message_length=first_message_length,
        last_message_length=last_message_length,
        avg_message_length=avg_message_length,
        summary_decision_reason=summary_decision_reason,
        execution_usage=execution_usage
    )

def record_summary_cache_hit():
    """Record a cache hit for summary"""
    METRICS_COLLECTOR.record_summary_cache_hit()

def record_summary_cache_miss():
    """Record a cache miss for summary"""
    METRICS_COLLECTOR.record_summary_cache_miss()

def record_summary_reuse():
    """Record when a summary is reused/consolidated"""
    METRICS_COLLECTOR.record_summary_reuse()
