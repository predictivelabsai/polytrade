from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, convert_to_messages
from langchain_deepseek import ChatDeepSeek

from .config import AgentSettings

MODEL_ID = "deepseek-v4-flash"
REASONING_EFFORT = "max"


class DeepSeekThinkingChat(ChatDeepSeek):
    """Preserve DeepSeek reasoning content required after tool calls.

    langchain-deepseek 1.1.0 parses ``reasoning_content`` from responses but its
    serializer drops that provider field on subsequent requests. DeepSeek
    rejects a tool continuation without it. This adapter adds only that missing
    wire field and otherwise delegates to the official integration.
    """

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        messages = convert_to_messages(input_)
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        wire_messages = payload.get("messages", [])

        for source, target in zip(messages, wire_messages, strict=False):
            if not isinstance(source, AIMessage) or not isinstance(target, dict):
                continue
            reasoning = source.additional_kwargs.get("reasoning_content")
            if reasoning is not None:
                target["reasoning_content"] = reasoning

        return payload


def build_model(settings: AgentSettings) -> DeepSeekThinkingChat:
    model = DeepSeekThinkingChat(
        model=MODEL_ID,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=str(settings.DEEPSEEK_BASE_URL).rstrip("/"),
        reasoning_effort=REASONING_EFFORT,
        extra_body={"thinking": {"type": "enabled"}},
        max_retries=3,
        timeout=120,
        streaming=True,
    )
    assert_model_configuration(model)
    return model


def assert_model_configuration(model: DeepSeekThinkingChat) -> None:
    params = model._default_params
    if params.get("model") != MODEL_ID:
        raise RuntimeError("DeepSeek model drift detected")
    if params.get("reasoning_effort") != REASONING_EFFORT:
        raise RuntimeError("DeepSeek maximum reasoning is not configured")
    if params.get("extra_body") != {"thinking": {"type": "enabled"}}:
        raise RuntimeError("DeepSeek thinking mode is not enabled")
    if "temperature" in params or "top_p" in params:
        raise RuntimeError("Sampling parameters must not be sent in thinking mode")
    conformance = model._get_request_payload(
        [
            HumanMessage(content="adapter readiness probe"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "readiness_probe",
                        "args": {},
                        "id": "probe-call",
                        "type": "tool_call",
                    }
                ],
                additional_kwargs={"reasoning_content": "opaque-provider-state"},
            ),
            ToolMessage(content="ok", tool_call_id="probe-call"),
        ]
    )
    wire_messages = conformance.get("messages", [])
    if (
        len(wire_messages) < 2
        or wire_messages[1].get("reasoning_content") != "opaque-provider-state"
    ):
        raise RuntimeError("DeepSeek reasoning continuation protocol is not preserved")
