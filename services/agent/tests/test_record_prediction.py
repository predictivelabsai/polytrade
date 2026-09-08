import json
from typing import Any

import httpx
import pytest
import respx
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from polytrade_agent.context import AgentRunContext
from polytrade_agent.graph import build_agent
from polytrade_agent.model import DeepSeekThinkingChat
from polytrade_agent.schemas import PredictionInput, PredictionRecorded


class PredictionCallingDeepSeek(DeepSeekThinkingChat):
    _call_count: int = PrivateAttr(default=0)
    _prediction_args: dict[str, Any] = PrivateAttr(default_factory=dict)

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        self._call_count += 1
        if self._call_count == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "record_prediction",
                        "args": self._prediction_args,
                        "id": "prediction-call-1",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            message = AIMessage(content="Recorded the call.")
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager
        return self._generate(messages, stop=stop, **kwargs)


GATEWAY_RECORD = {
    "predictionId": "44444444-4444-4444-8444-444444444444",
    "conditionId": "0xcondition",
    "tokenId": "123",
    "marketQuestion": "Will the Fed hold rates in September?",
    "predictedOutcome": "Yes",
    "confidence": "0.62",
    "status": "PENDING",
    "madeAt": "2026-09-02T12:00:00.000Z",
    "category": None,
}


def test_prediction_input_rejects_out_of_bounds_values() -> None:
    with pytest.raises(ValueError):
        PredictionInput(
            condition_id="0xcondition",
            market_question="Will the Fed hold rates in September?",
            predicted_outcome="Yes",
            confidence="1.01",
        )
    with pytest.raises(ValueError):
        PredictionInput(
            condition_id="0xcondition",
            market_question="Will the Fed hold rates in September?",
            predicted_outcome="Yes",
            confidence="0.12345",
        )
    with pytest.raises(ValueError):
        PredictionInput(
            condition_id="0xcondition",
            market_question="Will the Fed hold rates in September?",
            predicted_outcome="Yes",
            # Not a secret — a malformed Polymarket token id; ruff's S106
            # pattern fires on the `token_id` keyword alone.
            token_id="abc",  # noqa: S106
        )
    # Confidence is optional and omitted fields stay omitted on the wire.
    payload = PredictionInput(
        condition_id="0xcondition",
        market_question="Will the Fed hold rates in September?",
        predicted_outcome="Yes",
    ).model_dump(by_alias=True, exclude_none=True)
    assert payload == {
        "conditionId": "0xcondition",
        "marketQuestion": "Will the Fed hold rates in September?",
        "predictedOutcome": "Yes",
    }


def test_prediction_recorded_parses_the_gateway_response() -> None:
    record = PredictionRecorded.model_validate(GATEWAY_RECORD)
    assert record.prediction_id is not None
    assert record.status == "PENDING"
    assert record.category is None


@pytest.mark.asyncio
async def test_record_prediction_forwards_bearer_idempotency_and_camel_case_body() -> None:
    model = PredictionCallingDeepSeek(
        model="deepseek-v4-flash",
        api_key="test",
        reasoning_effort="max",
        extra_body={"thinking": {"type": "enabled"}},
    )
    model._prediction_args = {
        "condition_id": "0xcondition",
        "market_question": "Will the Fed hold rates in September?",
        "predicted_outcome": "Yes",
        "token_id": "123",
        "confidence": "0.62",
    }
    agent = build_agent(model=model)
    bearer_fixture = "request-scoped-research-token"

    with respx.mock:
        route = respx.post("http://localhost:4000/v1/agent/predictions").mock(
            return_value=httpx.Response(200, json=GATEWAY_RECORD)
        )
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="Will the Fed hold rates? I think yes.")]},
            context=AgentRunContext(
                principal_id="assethero:user-123",
                scopes=("research",),
                gateway_bearer=bearer_fixture,
            ),
        )

    assert route.called
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {bearer_fixture}"
    assert request.headers["Idempotency-Key"] == "prediction:prediction-call-1"
    assert json.loads(request.content) == {
        "conditionId": "0xcondition",
        "marketQuestion": "Will the Fed hold rates in September?",
        "predictedOutcome": "Yes",
        "tokenId": "123",
        "confidence": "0.62",
    }
    tool_messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.name == "record_prediction"
    ]
    assert len(tool_messages) == 1
    assert "44444444-4444-4444-8444-444444444444" in tool_messages[0].content
    assert bearer_fixture not in repr(result)