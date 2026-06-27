"""Tool call accumulation and rewriting utilities.

This module provides utilities for accumulating incremental tool_calls deltas during
streaming responses, which is needed for proper OpenAI-compatible tool call handling.
"""

from typing import Any, Dict


class ToolCallAccumulator:
    """Accumulates incremental tool_calls deltas by index.
    
    During streaming, tool calls arrive as incremental updates (deltas) that
    need to be accumulated into complete tool call objects before being
    included in the final response.
    
    Example:
        Chunk 1: {"tool_calls": [{"index": 0, "type": "function", "function": {"name": "read"}}]}
        Chunk 2: {"tool_calls": [{"index": 0, "function": {"arguments": "{\n  ...\n}"}}]}
        
        Result: {"index": 0, "type": "function", "function": {"name": "read", "arguments": "{\n  ...\n}"}}
    """
    
    def __init__(self):
        """Initialize empty accumulator."""
        self.accumulators: Dict[int, Dict[str, Any]] = {}
    
    def add_delta(self, chunk: Dict[str, Any]) -> None:
        """Add tool call deltas from a streaming chunk.
        
        Args:
            chunk: Streaming delta that may contain tool_calls array
        """
        if "tool_calls" not in chunk:
            return
        
        for tc_delta in chunk["tool_calls"]:
            idx = tc_delta.get("index")
            if idx is None:
                continue
            
            if idx not in self.accumulators:
                self.accumulators[idx] = {}
            
            # Deep merge delta into accumulator
            self._merge_delta(self.accumulators[idx], tc_delta)
    
    def _merge_delta(self, target: Dict[str, Any], delta: Dict[str, Any]) -> None:
        """Merge a delta into the target accumulator.
        
        Args:
            target: Target dict to merge into
            delta: Delta dict to merge
        """
        for key, value in delta.items():
            if key not in target:
                target[key] = value
            elif isinstance(value, dict) and isinstance(target[key], dict):
                # Recursively merge nested dicts
                self._merge_delta(target[key], value)
            else:
                # Override with new value (string concatenation for tool calls)
                if isinstance(target[key], str) and isinstance(value, str):
                    target[key] = target[key] + value
                else:
                    target[key] = value
    
    def build_final_calls(self, idx: int) -> Dict[str, Any]:
        """Build complete tool call dict for a specific index.
        
        Args:
            idx: Index of the tool call to build
            
        Returns:
            Complete tool call dictionary with index field added
        """
        if idx not in self.accumulators:
            return {}
        
        tc_data = self.accumulators[idx].copy()
        tc_data["index"] = idx
        return tc_data
    
    def clear(self) -> None:
        """Clear all accumulated data."""
        self.accumulators.clear()
