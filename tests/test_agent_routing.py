from chat.routing import DEFAULT_RUNTIME, route_message
from chat.runtimes import HermesAgent


def test_unprefixed_messages_use_deepagents_by_default():
    route = route_message("  what moved this market?  ")
    assert route.runtime == DEFAULT_RUNTIME == "deepagents"
    assert route.content == "what moved this market?"
    assert route.requested_runtime is None


def test_explicit_runtime_prefixes_are_one_message_overrides():
    assert route_message("/hermes analyze this").runtime == "hermes"
    assert route_message("/deepagent analyze this").runtime == "deepagents"
    assert route_message("/deepagents analyze this").runtime == "deepagents"
    assert route_message("/HERMES Analyze this").content == "Analyze this"


def test_prefix_must_be_the_first_complete_token():
    assert route_message("explain /hermes").requested_runtime is None
    assert route_message("/hermesx explain").requested_runtime is None


def test_empty_override_is_preserved_for_local_help():
    route = route_message("/hermes")
    assert route.runtime == "hermes"
    assert route.content == ""


async def test_hermes_uses_private_key_and_stable_owned_session(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage

    captured = {}

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"answer"}}]}'
            yield "data: [DONE]"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return Response()

    monkeypatch.setenv("HERMES_API_SERVER_KEY", "test-key")
    monkeypatch.setenv("HERMES_API_URL", "http://hermes.test/v1")
    monkeypatch.setenv("HERMES_PERSIST_SESSIONS", "true")
    monkeypatch.setattr("chat.runtimes.httpx.AsyncClient", lambda **kwargs: Client())
    agent = HermesAgent(user_id="user-1", thread_id="thread-1")
    events = [event async for event in agent.astream_events({"messages": [
        HumanMessage(content="old"), AIMessage(content="old answer"),
        HumanMessage(content="new question")
    ]}, "v2")]

    assert captured["url"] == "http://hermes.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["headers"]["X-Hermes-Session-Id"] == "polytrade:thread-1"
    assert captured["headers"]["X-Hermes-Session-Key"] == "polytrade-user:user-1"
    assert captured["json"]["messages"] == [{"role": "user", "content": "new question"}]
    assert events[0]["data"]["chunk"].content == "answer"


def test_default_factory_builds_deepagents_without_invoking_a_model(monkeypatch):
    import deepagents
    from chat import agent_factory
    from model.llm import LLMProvider

    captured = {}
    sentinel = object()

    def create(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(deepagents, "create_deep_agent", create)
    monkeypatch.setattr(LLMProvider, "get_model", lambda *args, **kwargs: "fake-model")
    monkeypatch.setattr(agent_factory, "get_chat_tools", lambda: ("safe-tool",))
    agent_factory.get_chat_agent.cache_clear()
    try:
        assert agent_factory.get_chat_agent() is sentinel
    finally:
        agent_factory.get_chat_agent.cache_clear()
    assert captured["model"] == "fake-model"
    assert captured["tools"] == ["safe-tool"]
    assert "system_prompt" in captured
