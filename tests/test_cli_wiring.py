from workflow_ai.cli import _make_backend
from workflow_ai.backends.anthropic_sdk import AnthropicBackend
from workflow_ai.backends.openai_sdk import OpenAIBackend
import pytest, sys, types

# Inject fake SDK modules so backend __init__ doesn't fail on import
for name in ("anthropic", "openai"):
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)

def test_make_backend_anthropic():
    # Can construct without raising (lazy import succeeds with fake module)
    # But __init__ calls anthropic.Anthropic() which needs a fake class
    # So just assert the return type
    import types, sys
    fake = sys.modules["anthropic"]
    fake.Anthropic = lambda **kw: types.SimpleNamespace(messages=types.SimpleNamespace(create=None))
    b = _make_backend("anthropic", None, "key", "model-id")
    assert isinstance(b, AnthropicBackend)

def test_make_backend_openai():
    import types, sys
    fake = sys.modules["openai"]
    fake.OpenAI = lambda **kw: types.SimpleNamespace(chat=None)
    b = _make_backend("openai", None, "key", "model-id")
    assert isinstance(b, OpenAIBackend)

def test_make_backend_openai_azure():
    import types, sys
    constructed = {}
    class FakeAzure:
        def __init__(self, **kw):
            constructed.update(kw)
            self.chat = None
    fake = sys.modules["openai"]
    fake.AzureOpenAI = FakeAzure
    b = _make_backend("openai", None, "key", "dep1", azure_endpoint="https://x.openai.azure.com", api_version="2024-10-21")
    assert isinstance(b, OpenAIBackend)
    assert constructed.get("azure_endpoint") == "https://x.openai.azure.com"
    assert constructed.get("api_version") == "2024-10-21"

def test_make_backend_unknown():
    with pytest.raises(Exception, match="unknown backend"):
        _make_backend("bogus", None, None, None)

def test_make_backend_default_headers_passed(monkeypatch):
    import types, sys
    received = {}
    class FakeAnthropicCapture:
        def __init__(self, **kw):
            received.update(kw)
            self.messages = None
    sys.modules["anthropic"].Anthropic = FakeAnthropicCapture
    _make_backend("anthropic", None, "key", "m", default_headers={"X-Token": "abc"})
    assert received.get("default_headers") == {"X-Token": "abc"}
