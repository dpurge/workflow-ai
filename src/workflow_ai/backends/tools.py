from __future__ import annotations

import ipaddress
import os
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus, urlparse

import httpx

MAX_FILE_BYTES = 1_000_000
MAX_FETCH_BYTES = 500_000
HTTP_TIMEOUT = 20


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]


class ToolError(RuntimeError):
    pass


def _read_file(args: dict[str, Any]) -> str:
    path = args["path"]
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        raise ToolError(f"file not found: {path}")
    except OSError as exc:
        raise ToolError(f"cannot read {path}: {exc}")
    text = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    return text


def _write_file(args: dict[str, Any]) -> str:
    raw_path: str = args["path"]

    if os.path.isabs(raw_path) or (
        len(raw_path) >= 2 and raw_path[1] == ":"
    ):
        raise ToolError(f"write path must be relative, not absolute: {raw_path}")

    cwd = Path(os.getcwd()).resolve()
    target = (cwd / raw_path).resolve()

    if not (str(target).startswith(str(cwd) + os.sep) or target == cwd):
        raise ToolError(f"write path escapes working directory: {raw_path}")

    content: str = args["content"]
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"write failed for {raw_path}: {exc}")

    return f"wrote {len(content.encode('utf-8'))} bytes to {raw_path}"


class _DDGParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        cls = attr_map.get("class", "") or ""
        if tag == "a" and "result__a" in cls:
            href = attr_map.get("href", "") or ""
            self._current = {"title": "", "url": href, "snippet": ""}
            self._capture_title = True
            return
        if tag == "a" and "result__snippet" in cls:
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            if self._capture_title:
                self._capture_title = False
                if self._current is not None:
                    self._results.append(self._current)
                    self._current = None
            elif self._capture_snippet:
                self._capture_snippet = False

    def handle_data(self, data: str) -> None:
        if self._capture_title and self._current is not None:
            self._current["title"] += data
        if self._capture_snippet and self._results:
            self._results[-1]["snippet"] += data


def _web_search(args: dict[str, Any]) -> str:
    query: str = args["query"]
    max_results: int = int(args.get("max_results", 5))
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    try:
        resp = httpx.get(
            url,
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return f"web search failed: {exc}"

    parser = _DDGParser()
    try:
        parser.feed(resp.text)
    except Exception as exc:
        return f"web search parse failed: {exc}"

    results = parser._results[:max_results]
    if not results:
        return "no results found"

    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title'].strip()}")
        lines.append(f"   URL: {r['url']}")
        snippet = r["snippet"].strip()
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _web_fetch(args: dict[str, Any]) -> str:
    raw_url: str = args["url"]
    parsed = urlparse(raw_url)

    if parsed.scheme not in ("http", "https"):
        raise ToolError(f"URL must use http or https scheme: {raw_url}")

    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
        if any(addr in net for net in _PRIVATE_NETWORKS):
            raise ToolError(f"URL targets a private/reserved IP address: {host}")
    except ValueError:
        pass

    try:
        resp = httpx.get(raw_url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ToolError(f"HTTP {exc.response.status_code} fetching {raw_url}")
    except httpx.HTTPError as exc:
        raise ToolError(f"fetch failed: {exc}")

    content_type = resp.headers.get("content-type", "")
    if "html" in content_type or "text" in content_type:
        extractor = _TextExtractor()
        try:
            extractor.feed(resp.text)
            text = extractor.get_text()
        except Exception:
            text = resp.text
    else:
        text = resp.text

    return text[:MAX_FETCH_BYTES]


TOOL_REGISTRY: dict[str, ToolDef] = {
    "Read": ToolDef(
        name="Read",
        description="Read a file from the filesystem and return its contents.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative file path to read",
                }
            },
            "required": ["path"],
        },
        handler=_read_file,
    ),
    "Write": ToolDef(
        name="Write",
        description="Write content to a file within the current working directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to write (must be within current working directory)",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
            },
            "required": ["path", "content"],
        },
        handler=_write_file,
    ),
    "WebSearch": ToolDef(
        name="WebSearch",
        description="Search the web using DuckDuckGo and return titles, URLs, and snippets.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        handler=_web_search,
    ),
    "WebFetch": ToolDef(
        name="WebFetch",
        description="Fetch a URL and return its text content.",
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch (must be http or https)",
                }
            },
            "required": ["url"],
        },
        handler=_web_fetch,
    ),
}


def resolve_tools(allowed: list[str]) -> list[ToolDef]:
    result: list[ToolDef] = []
    for name in allowed:
        if name in TOOL_REGISTRY:
            result.append(TOOL_REGISTRY[name])
        else:
            print(f"tools.py: unknown tool name skipped: {name!r}", file=sys.stderr)
    return result


def anthropic_tool_specs(tools: list[ToolDef]) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


def openai_tool_specs(tools: list[ToolDef]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def dispatch(tools: list[ToolDef], name: str, args: dict[str, Any]) -> str:
    for tool in tools:
        if tool.name == name:
            try:
                return tool.handler(args)
            except ToolError as exc:
                return str(exc)
    return f"unknown tool: {name}"
