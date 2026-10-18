"""Pure tool-signature and history matching shared across serving modes."""

import json
from difflib import SequenceMatcher


def signature(name: str, arguments: str) -> tuple[str, str]:
    try:
        arguments = json.dumps(json.loads(arguments), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        pass
    return name, arguments


def repeats(
    prior,
    current,
    *,
    max_repeats=1,
    fuzzy_max_repeats=0,
    fuzzy_look_back=0,
    fuzzy_threshold=None,
    ab_loop_detection=False,
):
    consecutive = 0
    for previous in reversed(prior):
        if previous != current:
            break
        consecutive += 1
    if max_repeats > 0 and consecutive >= max_repeats:
        return True
    window = prior[-fuzzy_look_back:] if fuzzy_look_back > 0 else prior
    if fuzzy_max_repeats > 0 and sum(item == current for item in window) >= fuzzy_max_repeats:
        return True
    if fuzzy_threshold is not None and prior:
        previous = prior[-1]
        if previous[0] == current[0] and SequenceMatcher(None, previous[1], current[1]).ratio() >= fuzzy_threshold:
            return True
    return bool(
        ab_loop_detection
        and len(prior) >= 3
        and prior[-3] == prior[-1]
        and prior[-2] == current
        and prior[-1] != current
    )
