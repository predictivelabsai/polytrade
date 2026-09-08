import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from polytrade_agent.config import AgentSettings, enforce_no_langsmith, get_settings
from polytrade_agent.model import MODEL_ID, REASONING_EFFORT, build_model


def test_model_is_fixed_to_maximum_reasoning() -> None:
    settings = get_settings()
    model = build_model(settings)
    params = model._default_params

    assert params["model"] == MODEL_ID
    assert params["reasoning_effort"] == REASONING_EFFORT == "max"
    assert params["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in params
    assert "top_p" not in params
    assert settings.BACKTEST_MAX_ACTIVE_RUNS_PER_OWNER == 10


def test_langsmith_runtime_is_disabled_and_credentials_fail_closed() -> None:
    environment: dict[str, str] = {}
    enforce_no_langsmith(environment)
    assert environment["LANGSMITH_TRACING"] == "false"
    assert environment["LANGCHAIN_TRACING_V2"] == "false"
    assert environment["LANGGRAPH_CLI_NO_ANALYTICS"] == "1"

    environment["LANGSMITH_API_KEY"] = "forbidden"
    with pytest.raises(RuntimeError, match="LANGSMITH_API_KEY"):
        enforce_no_langsmith(environment)

    with pytest.raises(RuntimeError, match="LANGSMITH_TRACING"):
        enforce_no_langsmith({"LANGSMITH_TRACING": "true"})


def test_checkpoint_key_and_cors_origins_fail_closed() -> None:
    values = get_settings().model_dump()
    with pytest.raises(ValidationError, match="32 UTF-8 bytes"):
        AgentSettings.model_validate({**values, "LANGGRAPH_AES_KEY": "too-short"})
    with pytest.raises(ValidationError, match="exact HTTP"):
        AgentSettings.model_validate({**values, "CORS_ORIGINS": "*"})
    with pytest.raises(ValidationError, match="paths"):
        AgentSettings.model_validate(
            {**values, "CORS_ORIGINS": "https://assethero.test/path"}
        )


def test_assethero_api_trust_is_optional_but_requires_a_complete_pair() -> None:
    values = get_settings().model_dump()
    standalone = AgentSettings.model_validate(
        {
            **values,
            "ASSETHERO_API_ISSUER": None,
            "ASSETHERO_API_JWKS_URL": None,
        }
    )
    assert standalone.ASSETHERO_API_ISSUER is None
    assert standalone.ASSETHERO_API_JWKS_URL is None

    with pytest.raises(ValidationError, match="configured together"):
        AgentSettings.model_validate(
            {
                **values,
                "ASSETHERO_API_JWKS_URL": None,
            }
        )


def test_reasoning_content_round_trips_after_tool_call() -> None:
    model = build_model(get_settings())
    messages = [
        HumanMessage(content="Find a market"),
        AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {}, "id": "call-1", "type": "tool_call"}],
            additional_kwargs={"reasoning_content": "provider protocol state"},
        ),
        ToolMessage(content="result", tool_call_id="call-1"),
    ]

    payload = model._get_request_payload(messages)

    assert payload["messages"][1]["reasoning_content"] == "provider protocol state"
    assert payload["reasoning_effort"] == "max"


def test_two_tool_continuations_and_resumed_turn_keep_max_reasoning() -> None:
    model = build_model(get_settings())
    history = [
        HumanMessage(content="Compare a market"),
        AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {}, "id": "call-1", "type": "tool_call"}],
            additional_kwargs={"reasoning_content": "first private protocol state"},
        ),
        ToolMessage(content="first result", tool_call_id="call-1"),
        AIMessage(
            content="",
            tool_calls=[{"name": "book", "args": {}, "id": "call-2", "type": "tool_call"}],
            additional_kwargs={"reasoning_content": "second private protocol state"},
        ),
        ToolMessage(content="second result", tool_call_id="call-2"),
        AIMessage(content="Public answer"),
        HumanMessage(content="Now revisit it"),
    ]

    streaming = model._get_request_payload(history, stream=True)
    resumed = model._get_request_payload(history)

    for payload in (streaming, resumed):
        assert payload["reasoning_effort"] == "max"
        assert payload["extra_body"] == {"thinking": {"type": "enabled"}}
        assert payload["messages"][1]["reasoning_content"] == "first private protocol state"
        assert payload["messages"][3]["reasoning_content"] == "second private protocol state"
        assert "temperature" not in payload
        assert "top_p" not in payload
