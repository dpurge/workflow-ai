from __future__ import annotations

import httpx
import pytest

from workflow_ai.backends.tools import (
    MAX_FETCH_BYTES,
    MAX_FILE_BYTES,
    ToolError,
    _read_file,
    _web_fetch,
    _web_search,
    _write_file,
    anthropic_tool_specs,
    dispatch,
    openai_tool_specs,
    resolve_tools,
)


class MockResponse:
    def __init__(self, text, status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def test_read_file_basic(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello world")
    result = _read_file({"path": str(f)})
    assert result == "hello world"


def test_read_file_missing():
    with pytest.raises(ToolError):
        _read_file({"path": "/nonexistent/xyz.txt"})


def test_read_file_truncation(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * (MAX_FILE_BYTES + 100))
    result = _read_file({"path": str(f)})
    assert len(result) == MAX_FILE_BYTES


def test_write_file_creates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_file({"path": "subdir/out.txt", "content": "hello"})
    assert (tmp_path / "subdir" / "out.txt").read_text() == "hello"


def test_write_file_absolute_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ToolError):
        _write_file({"path": "/etc/passwd", "content": "x"})


def test_write_file_escape_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ToolError):
        _write_file({"path": "../../etc/passwd", "content": "x"})


def test_web_search_parses_results(monkeypatch):
    html = (
        '<html><body>'
        '<a class="result__a" href="https://example.com">Example Title</a>'
        '<a class="result__snippet">A useful snippet</a>'
        '</body></html>'
    )
    monkeypatch.setattr(
        "workflow_ai.backends.tools.httpx.get",
        lambda *a, **kw: MockResponse(html),
    )
    result = _web_search({"query": "test"})
    assert "Example Title" in result
    assert "https://example.com" in result


def test_web_search_http_error(monkeypatch):
    def raise_error(*a, **kw):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr("workflow_ai.backends.tools.httpx.get", raise_error)
    result = _web_search({"query": "test"})
    assert "web search failed" in result


def test_web_fetch_strips_html(monkeypatch):
    html = "<html><body><p>Hello world</p><script>bad</script></body></html>"
    monkeypatch.setattr(
        "workflow_ai.backends.tools.httpx.get",
        lambda *a, **kw: MockResponse(html, status_code=200, headers={"content-type": "text/html"}),
    )
    result = _web_fetch({"url": "https://example.com"})
    assert "Hello world" in result
    assert "<p>" not in result
    assert "bad" not in result


def test_web_fetch_private_ip_rejected():
    with pytest.raises(ToolError):
        _web_fetch({"url": "http://192.168.1.1/secret"})


def test_web_fetch_loopback_rejected():
    with pytest.raises(ToolError):
        _web_fetch({"url": "http://127.0.0.1/secret"})


def test_web_fetch_bad_scheme():
    with pytest.raises(ToolError):
        _web_fetch({"url": "ftp://example.com/file"})


def test_web_fetch_truncation(monkeypatch):
    monkeypatch.setattr(
        "workflow_ai.backends.tools.httpx.get",
        lambda *a, **kw: MockResponse(
            "x" * (MAX_FETCH_BYTES + 1000),
            status_code=200,
            headers={"content-type": "text/plain"},
        ),
    )
    result = _web_fetch({"url": "https://example.com"})
    assert len(result) == MAX_FETCH_BYTES


def test_resolve_tools_known():
    tools = resolve_tools(["Read", "Write"])
    assert len(tools) == 2
    assert tools[0].name == "Read"


def test_resolve_tools_unknown_skipped(capsys):
    tools = resolve_tools(["Read", "Bogus"])
    assert len(tools) == 1
    captured = capsys.readouterr()
    assert "Bogus" in captured.err


def test_anthropic_tool_specs_shape():
    tools = resolve_tools(["Read"])
    specs = anthropic_tool_specs(tools)
    assert specs[0].keys() == {"name", "description", "input_schema"}


def test_openai_tool_specs_shape():
    tools = resolve_tools(["Read"])
    specs = openai_tool_specs(tools)
    assert specs[0]["type"] == "function"
    assert "name" in specs[0]["function"]
    assert "parameters" in specs[0]["function"]


def test_dispatch_known(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("hi")
    tools = resolve_tools(["Read"])
    result = dispatch(tools, "Read", {"path": str(tmp_path / "f.txt")})
    assert result == "hi"


def test_dispatch_unknown():
    result = dispatch([], "Bogus", {})
    assert "unknown tool" in result


def test_dispatch_tool_error_returned_as_string():
    tools = resolve_tools(["Read"])
    result = dispatch(tools, "Read", {"path": "/no/such/file.txt"})
    assert "not found" in result
