"""Workflow run logger.

Implements the on_event(kind, node_id, data) callback contract used by Engine.
Writes human-readable event lines to stdout (when verbose=True) and/or a log
file (when log_file is set). Both destinations receive identical output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO


_LABELS: dict[str, str] = {
    "enter":      "enter     ",
    "attempt":    "attempt   ",
    "retry":      "retry     ",
    "output":     "output    ",
    "context":    "context   ",
    "transition": "transition",
    "terminal":   "terminal  ",
}


def _format_line(kind: str, node_id: str, data: dict[str, Any]) -> str:
    label = _LABELS.get(kind, f"{kind:<10}")
    parts = [f"[{label}]  {node_id}"]

    if kind == "attempt":
        parts.append(f"  attempt {data['attempt']}/{data['retries']}")
    elif kind == "retry":
        parts.append(f"  attempt {data['attempt']} failed: {data['error']}")
    elif kind == "output":
        parts.append(f"  {json.dumps(data['raw_output'], ensure_ascii=False)}")
    elif kind == "context":
        parts.append(f"  {json.dumps(data['context_data'], ensure_ascii=False)}")
    elif kind == "transition":
        successors = ", ".join(data.get("successors", []))
        parts.append(f"  → {successors}")
    elif kind == "terminal":
        parts.append(f"  {json.dumps(data['context_data'], ensure_ascii=False)}")

    return "".join(parts)


class RunLogger:
    """Observability callback for Engine.run().

    verbose=True  → writes to stdout
    log_file      → writes to that file (in addition to stdout when verbose,
                    or file-only when verbose=False)
    """

    def __init__(self, verbose: bool = False, log_file: Path | None = None) -> None:
        self.verbose = verbose
        self.log_file = log_file
        self._file: TextIO | None = None
        if log_file is not None:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            self._file = log_file.open("w", encoding="utf-8")

    def __call__(self, kind: str, node_id: str, data: dict[str, Any] | None = None) -> None:
        if not self.verbose and self._file is None:
            return
        line = _format_line(kind, node_id, data or {})
        if self.verbose:
            print(line, file=sys.stdout, flush=True)
        if self._file is not None:
            print(line, file=self._file, flush=True)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "RunLogger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
