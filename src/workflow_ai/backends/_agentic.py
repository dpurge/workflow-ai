from __future__ import annotations

MAX_TURNS_CAP = 25


def effective_max_turns(inv_max_turns: int | None, default: int = 10) -> int:
    n = inv_max_turns if inv_max_turns is not None else default
    return min(n, MAX_TURNS_CAP)
