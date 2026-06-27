"""
Structured logging for filter pipeline events.

This module provides dedicated logging for filter-related events,
enabling monitoring and debugging of filter behavior.

Features:
- Filter-specific log files (e.g., logs/filters/model_nudge.log)
- Structured JSON format for easy parsing/monitoring
- Separate from main server logs to avoid noise
- Configurable log levels per filter
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class FilterLogger:
    """Logger for filter pipeline events.
    
    Creates dedicated log files for each filter and supports
    structured JSON output for monitoring/analysis.
    
    Example usage:
        logger = FilterLogger("model_nudge")
        logger.nudge_triggered(trigger_pattern=":$", nudge_attempt=1, response_content="Now I will:")
        logger.filter_chain_executed(filters=["master_prompt", "loop_detector", "model_nudge"])
    """

    def __init__(self, filter_name: str, log_dir: Optional[str] = None):
        """Initialize filter logger.
        
        Args:
            filter_name: Name of the filter (e.g., "model_nudge")
            log_dir: Directory for log files (default: logs/filters/)
        """
        self.filter_name = filter_name
        
        # Default log directory
        if log_dir is None:
            base_dir = Path(__file__).parent.parent.parent
            log_dir = base_dir / "logs" / "filters"
        
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / f"{filter_name}.log"

    def _write_log(self, event: Dict[str, Any]) -> None:
        """Write structured log entry to filter-specific file.

        Uses the AsyncLogWriter when available to avoid blocking the event loop.
        Falls back to synchronous open()+write()+close when the writer is not
        running (early startup, tests).
        """
        # Add metadata
        event["timestamp"] = datetime.utcnow().isoformat() + "Z"
        event["filter"] = self.filter_name

        # Try async writer first
        try:
            from ..async_log_writer import get_async_writer
            writer = get_async_writer()
            if writer._running:
                sink_name = f"filter_{self.filter_name}"
                if sink_name not in writer._sinks:
                    writer.register_sink(sink_name, str(self.log_file))
                writer.enqueue(sink_name, event)
                return
        except Exception:
            pass  # fall through to sync write

        # Synchronous fallback
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def nudge_triggered(
        self,
        trigger_pattern: str,
        response_content: str,
        nudge_attempt: int,
        action: str = "nudge",
        max_attempts: int = 3,
    ) -> None:
        """Log when a nudge is triggered.
        
        Args:
            trigger_pattern: Regex pattern that matched
            response_content: The lazy response content (truncated to 200 chars)
            nudge_attempt: Which nudge attempt this is (1-indexed)
            action: "nudge" or "regenerate"
            max_attempts: Maximum allowed nudges
        """
        self._write_log({
            "event": "nudge_triggered",
            "trigger_pattern": trigger_pattern,
            "response_content": response_content[:200],  # Truncate for log
            "nudge_attempt": nudge_attempt,
            "action": action,
            "max_attempts": max_attempts,
        })

    def loop_detected(
        self,
        duplicate_count: int,
        window_size: int,
        response_hash: str,
    ) -> None:
        """Log when a loop is detected.
        
        Args:
            duplicate_count: Number of consecutive duplicates found
            window_size: Size of detection window
            response_hash: Hash of the repeated response
        """
        self._write_log({
            "event": "loop_detected",
            "duplicate_count": duplicate_count,
            "window_size": window_size,
            "response_hash": response_hash,
        })


    # TLS Tool Loop Stopper methods

    def tool_loop_detected(self, function_name: str, args_hash: str, attempt: int = 1) -> None:
        self._write_log({
            "event": "tool_loop_detected",
            "function_name": function_name,
            "args_hash": args_hash,
            "attempt": attempt,
        })

    def tls_intervention(self, messages_count: int) -> None:
        self._write_log({
            "event": "tls_intervention",
            "messages_count": messages_count,
        })

    def tls_retry(self, model: str, messages_count: int) -> None:
        self._write_log({
            "event": "tls_retry",
            "model": model,
            "messages_count": messages_count,
        })

    def tls_fallback(self, reason: str) -> None:
        self._write_log({
            "event": "tls_fallback",
            "reason": reason,
        })

    def master_prompt_injected(
        self,
        prompt_position: str,  # "prefix", "suffix", or "inject"
        prompt_length: int,
    ) -> None:
        """Log when a master prompt is injected.
        
        Args:
            prompt_position: Where the prompt was injected
            prompt_length: Length of injected prompt in characters
        """
        self._write_log({
            "event": "master_prompt_injected",
            "prompt_position": prompt_position,
            "prompt_length": prompt_length,
        })

    def filter_chain_executed(
        self,
        filters_executed: list[str],
        total_filters: int,
        nudge_count: int = 0,
        loop_count: int = 0,
    ) -> None:
        """Log when a complete filter chain execution finishes.
        
        Args:
            filters_executed: List of filter names that were executed
            total_filters: Total number of filters in the chain
            nudge_count: Number of nudges triggered during execution
            loop_count: Number of loops detected during execution
        """
        self._write_log({
            "event": "filter_chain_executed",
            "filters_executed": filters_executed,
            "total_filters": total_filters,
            "nudge_count": nudge_count,
            "loop_count": loop_count,
        })

    def filter_disabled(self) -> None:
        """Log when a filter is disabled."""
        self._write_log({
            "event": "filter_disabled",
        })

    def filter_error(self, error_type: str, message: str) -> None:
        """Log an error in the filter.

        Args:
            error_type: Type of error (e.g., "ValidationError", "ExecutionError")
            message: Error description
        """
        self._write_log({
            "event": "filter_error",
            "error_type": error_type,
            "message": message,
        })

    # ── RLS methods ─────────────────────────────────────────────────

    def reasoning_loop_detected(self, reasoning: str) -> None:
        """Log when RLS detects a reasoning loop."""
        self._write_log({
            "event": "reasoning_loop_detected",
            "reasoning": reasoning,
        })

    def rls_intervention(self, messages_count: int) -> None:
        """Log RLS intervention (retry with augmented messages)."""
        self._write_log({
            "event": "rls_intervention",
            "messages_count": messages_count,
        })

    def rls_fallback(self) -> None:
        """Log when RLS falls back after exhausting retries."""
        self._write_log({
            "event": "rls_fallback",
        })

    # ── ToolRewrite methods ──────────────────────────────────────────

    def tool_rewrite_applied(self, tool_name: str, original_length: int, cleaned_length: int) -> None:
        """Log when tool_rewrite converts XML to structured tool_calls."""
        self._write_log({
            "event": "tool_rewrite_applied",
            "tool_name": tool_name,
            "original_length": original_length,
            "cleaned_length": cleaned_length,
        })

    def summary_stats(self) -> Dict[str, Any]:
        """Generate summary statistics from log file.
        
        Returns:
            Dictionary containing event counts and statistics
        """
        stats = {
            "filter_name": self.filter_name,
            "total_events": 0,
            "event_counts": {},
            "last_event_timestamp": None,
        }
        
        if not self.log_file.exists():
            return stats
        
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        stats["total_events"] += 1
                        
                        # Count events by type
                        event_type = event.get("event", "unknown")
                        stats["event_counts"][event_type] = \
                            stats["event_counts"].get(event_type, 0) + 1
                        
                        # Track last timestamp
                        ts = event.get("timestamp")
                        if ts:
                            stats["last_event_timestamp"] = ts
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        
        return stats


# Global registry of filter loggers for centralized access

    # System Prompt methods
    def system_prompt_inserted(self, prompt_preview: str) -> None:
        self._write_log({"event": "system_prompt_inserted", "prompt_preview": prompt_preview[:80]})
    def system_prompt_overridden(self, prompt_preview: str, old_length: int) -> None:
        self._write_log({"event": "system_prompt_overridden", "prompt_preview": prompt_preview[:80], "old_length": old_length})
    def system_prompt_prepended(self, prompt_preview: str, old_length: int) -> None:
        self._write_log({"event": "system_prompt_prepended", "prompt_preview": prompt_preview[:80], "old_length": old_length})

_filter_loggers: Dict[str, FilterLogger] = {}


def get_filter_logger(filter_name: str, log_dir: Optional[str] = None) -> FilterLogger:
    """Get or create a filter logger (singleton pattern).
    
    Args:
        filter_name: Name of the filter
        log_dir: Directory for log files
        
    Returns:
        FilterLogger instance for this filter
    """
    if filter_name not in _filter_loggers:
        _filter_loggers[filter_name] = FilterLogger(filter_name, log_dir)
    return _filter_loggers[filter_name]


def reset_filter_loggers() -> None:
    """Reset the global registry of filter loggers.
    
    Useful for testing or when log directory changes.
    """
    _filter_loggers.clear()
