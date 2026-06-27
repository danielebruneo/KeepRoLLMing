"""Prompt loading and rendering for summary generation."""

import os
from pathlib import Path
from typing import Optional, Dict, Any


# ---------------------------------------------------------------------
# Configuration imports (lazy)
# ---------------------------------------------------------------------
def _get_config():
    """Lazy import of config constants."""
    from .config import SUMMARY_PROMPT_DIR, SUMMARY_PROMPT_TYPE
    return {
        "SUMMARY_PROMPT_DIR": SUMMARY_PROMPT_DIR,
        "SUMMARY_PROMPT_TYPE": SUMMARY_PROMPT_TYPE,
    }


# ---------------------------------------------------------------------

DEFAULT_SUMMARY_PROMPTS: Dict[str, str] = {
    "classic": """You are an assistant that produces a CONTEXT SUMMARY for another model.

Objective:
compress the middle part of the conversation preserving facts, names, decisions, requests, constraints, TODOs, and useful technical details.

Rules:
- do not invent
- keep language coherent (prefer {{LANG_HINT}})
- be brief but dense
- use bullet points if useful
- include decisions and open TODOs
- no extra comments

Transcript:

=== TRANSCRIPT START ===
{{TRANSCRIPT}}
=== TRANSCRIPT END ===

RESPONSE:
only the final summary, no prefacing.
""",
    "structured": """You are a context compression engine.

Objective:
transform the middle part of the conversation into a structured and compact state, useful for continuing the dialogue correctly.

Rules:
- do not invent
- keep language coherent (prefer {{LANG_HINT}})
- prefer short bullet points
- clearly separate facts, decisions, constraints, and open tasks
- include technical details only if truly useful
- no introductory prose

Required output format:

[STATUS]

FACTS:
- ...

DECISIONS:
- ...

CONSTRAINTS:
- ...

OPEN_TASKS:
- ...

STYLE_NOTES:
- ...

[/STATUS]

Transcript:

=== TRANSCRIPT START ===
{{TRANSCRIPT}}
=== TRANSCRIPT END ===
""",
    "curated": """You are a context compaction engine.

Your task is NOT to simply summarize a conversation.

Your task is to produce a compressed reconstruction of the conversation that preserves the information necessary to continue the discussion correctly.

The reconstruction must balance:
- verbatim preservation of critical passages
- summarized sections for less critical spans
- a structured status snapshot

The output must be concise but faithful.

COMPACTION STRATEGY:
1. Preserve VERBATIM when content is critical:
   - instructions given to the assistant
   - constraints or rules
   - technical specifications
   - examples of style or tone
   - key decisions
   - code snippets
   - prompts or templates

2. Summarize when content is:
   - repetitive
   - exploratory discussion
   - background reasoning
   - intermediate brainstorming

3. Prefer short bullet summaries rather than prose.

4. Preserve the latest part of the provided transcript verbatim only if especially useful for continuity.
   Do NOT overuse verbatim excerpts.

5. Extract a STATUS block describing the current state.

OUTPUT FORMAT:

[INIT_INSTRUCTIONS_RAW]
Verbatim excerpts of important initial instructions or constraints.
Keep only the most relevant ones.
Maximum: 2 excerpts.
[/INIT_INSTRUCTIONS_RAW]

[ARCHIVE_SUMMARY]
Bullet summary of older conversation parts that are not critical to keep verbatim.
Focus on facts, reasoning steps, outcomes, technical constraints and conclusions.
Maximum: 10 bullets.
[/ARCHIVE_SUMMARY]

[KEY_EXCERPTS_RAW]
Short verbatim excerpts that are especially important to preserve.
Maximum: 3 excerpts.
[/KEY_EXCERPTS_RAW]

[STATUS]

FACTS:
- ...

DECISIONS:
- ...

CONSTRAINTS:
- ...

OPEN_TASKS:
- ...

STYLE_NOTES:
- ...

[/STATUS]

RULES:
- Be concise.
- Do NOT invent information.
- Do NOT repeat the entire transcript.
- Preserve key technical content if present.
- If unsure whether something is important, summarize it instead of preserving verbatim.
- Keep the output compact.

Transcript:

=== TRANSCRIPT START ===
{{TRANSCRIPT}}
=== TRANSCRIPT END ===
"""
}


def load_summary_prompt_template(prompt_type: Optional[str] = None) -> str:
    """Load a summary prompt template from config or file or fallback to default."""
    import sys
    
    # Check if we're being monkeypatched via rolling_summary module (test compatibility)
    rs_module = sys.modules.get('keeprollming.rolling_summary')
    
    # Always check for patched values first (for test compatibility)
    summary_prompt_dir = getattr(rs_module, 'SUMMARY_PROMPT_DIR', None) if rs_module else None
    summary_prompt_type = getattr(rs_module, 'SUMMARY_PROMPT_TYPE', None) if rs_module else None
    
    # If no patching detected, use config defaults
    if summary_prompt_dir is None:
        config = _get_config()
        summary_prompt_dir = config["SUMMARY_PROMPT_DIR"]
    if summary_prompt_type is None:
        config = _get_config()
        summary_prompt_type = config["SUMMARY_PROMPT_TYPE"]
    
    effective_type = (prompt_type or summary_prompt_type or "curated").strip()

    # If we have custom prompts defined in the configuration, check if this is one of them
    try:
        # Support monkeypatching via rolling_summary module (test compatibility)
        rs_module = sys.modules.get('keeprollming.rolling_summary')
        CONFIG = getattr(rs_module, 'CONFIG', None) if rs_module else None
        
        if CONFIG is None:
            from ..config import CONFIG as _CONFIG
            CONFIG = _CONFIG
            
        custom_prompts_config = CONFIG.get("custom_summary_prompts", {})

        # Only proceed with config-based prompt handling if it's a dict and has our effective_type
        if isinstance(custom_prompts_config, dict) and effective_type in custom_prompts_config:
            prompt_config = custom_prompts_config[effective_type]

            # If we have a file path string (relative to _prompts directory)
            # This handles paths that start with ./ or / or contain path separators
            if isinstance(prompt_config, str) and (prompt_config.startswith('./') or prompt_config.startswith('/') or '/' in prompt_config or '\\' in prompt_config):
                try:
                    path = Path(summary_prompt_dir) / prompt_config
                    return path.read_text(encoding="utf-8")
                except Exception:
                    pass  # Fall back to loading default prompts

            # For direct text prompts in config file or other values that should be treated literally
            elif isinstance(prompt_config, str):
                return prompt_config
    except ImportError:
        # CONFIG not available yet, skip custom prompt handling
        pass

    # Load from file for backward compatibility and other named prompts not defined in config
    path = Path(summary_prompt_dir) / f"{effective_type}.txt"
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        # fallback to default prompt if file not found or error occurs
        return DEFAULT_SUMMARY_PROMPTS.get(effective_type, DEFAULT_SUMMARY_PROMPTS["curated"])


def load_custom_prompt(prompt_text: Optional[str] = None) -> str:
    """Load custom summary prompt from text."""
    if prompt_text and isinstance(prompt_text, str):
        # If we have a custom prompt text, return it directly
        return prompt_text
    else:
        # Return empty string to indicate no custom prompt provided
        return ""


def render_summary_prompt(
    transcript: str,
    *,
    prompt_type: Optional[str] = None,
    lang_hint: str = "english",
) -> str:
    """Render a summary prompt with the given transcript."""
    # If we have both a specific prompt type and direct text, use the text directly as template
    if prompt_type is not None:
        custom_prompt = load_custom_prompt(prompt_type)
        if custom_prompt != "":
            # We're using a custom prompt provided in request
            return (
                custom_prompt
                .replace("{{TRANSCRIPT}}", transcript)
                .replace("{{LANG_HINT}}", lang_hint)
            )

    template = load_summary_prompt_template(prompt_type=prompt_type)

    return (
        template
        .replace("{{TRANSCRIPT}}", transcript)
        .replace("{{LANG_HINT}}", lang_hint)
    )


def get_summary_system_prompt(prompt_type: Optional[str] = None) -> str:
    """
    Keep system prompt small and stable.
    The real task instructions live in the file-based user template.
    """
    # Support monkeypatching via rolling_summary module (test compatibility)
    import sys
    rs_module = sys.modules.get('keeprollming.rolling_summary')
    summary_prompt_type = getattr(rs_module, 'SUMMARY_PROMPT_TYPE', None) if rs_module else None
    
    if summary_prompt_type is None:
        config = _get_config()
        summary_prompt_type = config["SUMMARY_PROMPT_TYPE"]
    
    effective_type = (prompt_type or summary_prompt_type or "curated").strip()

    if effective_type == "classic":
        return (
            "You are an assistant that compresses conversations for another model. "
            "Do not invent anything. Be faithful, compact, and useful."
        )

    if effective_type == "structured":
        return (
            "You are an assistant that transforms conversations into compact structured state. "
            "Do not invent anything. Keep only what is useful for continuing the conversation."
        )

    return (
        "You are a context compaction engine. "
        "Be faithful, compact, structured, and do not invent information."
    )


def render_incremental_summary_prompt(
    existing_summary: str,
    new_messages: list[Dict[str, Any]],
    *,
    lang_hint: str = "english",
) -> str:
    """Render prompt for incremental summary update."""
    from .decision_engine import render_messages_for_summary
    
    transcript = render_messages_for_summary(new_messages)

    # Support monkeypatching via rolling_summary module (test compatibility)
    import sys
    rs_module = sys.modules.get('keeprollming.rolling_summary')
    summary_prompt_dir = getattr(rs_module, 'SUMMARY_PROMPT_DIR', None) if rs_module else None
    
    if summary_prompt_dir is None:
        config = _get_config()
        summary_prompt_dir = config["SUMMARY_PROMPT_DIR"]

    # Load from file
    path = Path(summary_prompt_dir) / "incremental.txt"
    try:
        template = path.read_text(encoding="utf-8")
    except Exception:
        raise RuntimeError(f"Failed to load incremental prompt template from {path}")

    return (
        template
        .replace("{{EXISTING_SUMMARY}}", existing_summary)
        .replace("{{NEW_MESSAGES}}", transcript)
        .replace("{{LANG_HINT}}", lang_hint)
    )
