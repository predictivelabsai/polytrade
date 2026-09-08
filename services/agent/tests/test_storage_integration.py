import os
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import SecretStr

from polytrade_agent.config import get_settings
from polytrade_agent.storage import open_storage


class ConversationState(TypedDict):
    messages: Annotated[list[Any], add_messages]


async def reply(state: ConversationState) -> dict[str, list[AIMessage]]:
    latest = state["messages"][-1]
    return {
        "messages": [
            AIMessage(
                content=f"public:{latest.content}",
                additional_kwargs={"reasoning_content": "hidden-checkpoint-reasoning"},
            )
        ]
    }


async def fail_after_input(_state: ConversationState) -> dict[str, Any]:
    raise RuntimeError("intentional interrupted run")


def conversation_graph(checkpointer):
    builder = StateGraph(ConversationState)
    builder.add_node("reply", reply)
    builder.add_edge(START, "reply")
    return builder.compile(checkpointer=checkpointer)


def failing_graph(checkpointer):
    builder = StateGraph(ConversationState)
    builder.add_node("fail", fail_after_input)
    builder.add_edge(START, "fail")
    return builder.compile(checkpointer=checkpointer)


@pytest.mark.asyncio
async def test_postgres_schema_encryption_ownership_lock_and_restart() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    settings = get_settings().model_copy(update={"DATABASE_URL": SecretStr(database_url)})
    storage = await open_storage(settings)
    owner = "assethero:integration-user"
    account_fixture = "private-account-fixture"
    try:
        assert await storage.repository.schema_ready()
        async with storage.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT current_database() AS database_name,
                       current_schema() AS schema_name,
                       to_regclass('agent_threads')::text AS own_table,
                       to_regclass('backtest_runs')::text AS foreign_unqualified
                """
            )
            namespace = await cursor.fetchone()
        assert namespace["database_name"]
        assert namespace["schema_name"] == "polytrade_agent"
        assert namespace["own_table"] == "agent_threads"
        assert namespace["foreign_unqualified"] is None
        thread = await storage.repository.create_thread(owner)
        assert await storage.repository.get_owned_thread(thread.thread_id, owner)
        assert await storage.repository.get_owned_thread(thread.thread_id, "clerk:other") is None
        assert [item.thread_id for item in await storage.repository.list_owned_threads(owner)] == [
            thread.thread_id
        ]
        assert await storage.repository.list_owned_threads("clerk:other") == []
        await storage.repository.set_initial_title(
            thread.thread_id,
            owner,
            "Persistent market research",
        )
        titled = await storage.repository.get_owned_thread(thread.thread_id, owner)
        assert titled is not None
        assert titled.title == "Persistent market research"

        first_lease = await storage.repository.try_acquire_thread(thread.thread_id)
        assert first_lease is not None
        assert await storage.repository.try_acquire_thread(thread.thread_id) is None
        await first_lease.release()
        second_lease = await storage.repository.try_acquire_thread(thread.thread_id)
        assert second_lease is not None
        await second_lease.release()
        completed_run = await storage.repository.create_run(thread.thread_id, owner)
        graph = conversation_graph(storage.checkpointer)
        run_config = {"configurable": {"thread_id": str(completed_run)}}
        await graph.ainvoke(
            {"messages": [HumanMessage(content=account_fixture)]},
            config=run_config,
            durability="exit",
        )
        await storage.repository.commit_completed_run(completed_run, thread.thread_id, owner)

        config = {"configurable": {"thread_id": str(thread.thread_id)}}
        completed = await graph.aget_state(config)
        completed_messages = completed.values["messages"]
        second_completed_run = await storage.repository.create_run(thread.thread_id, owner)
        await graph.ainvoke(
            {
                "messages": [
                    *completed_messages,
                    HumanMessage(content="second committed turn"),
                ]
            },
            config={"configurable": {"thread_id": str(second_completed_run)}},
            durability="exit",
        )
        await storage.repository.commit_completed_run(
            second_completed_run,
            thread.thread_id,
            owner,
        )
        completed = await graph.aget_state(config)
        completed_messages = completed.values["messages"]
        assert any(message.content == "second committed turn" for message in completed_messages)
        async with storage.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT count(*) AS count FROM checkpoints WHERE thread_id = %s",
                (str(second_completed_run),),
            )
            assert (await cursor.fetchone())["count"] == 0

        interrupted_run = await storage.repository.create_run(thread.thread_id, owner)
        interrupted_config = {"configurable": {"thread_id": str(interrupted_run)}}
        with pytest.raises(RuntimeError, match="intentional interrupted run"):
            await failing_graph(storage.checkpointer).ainvoke(
                {
                    "messages": [
                        *completed_messages,
                        HumanMessage(content="uncommitted interrupted turn"),
                    ]
                },
                config=interrupted_config,
                durability="exit",
            )
        previous = await graph.aget_state(config)
        assert all(
            message.content != "uncommitted interrupted turn"
            for message in previous.values["messages"]
        )

        async with storage.pool.connection() as connection:
            blob_cursor = await connection.execute(
                """
                SELECT blob FROM checkpoint_blobs WHERE thread_id = %s
                UNION ALL
                SELECT blob FROM checkpoint_writes WHERE thread_id = %s
                """,
                (str(thread.thread_id), str(thread.thread_id)),
            )
            blobs = [row["blob"] for row in await blob_cursor.fetchall() if row["blob"]]
            checkpoint_cursor = await connection.execute(
                """
                SELECT checkpoint::text || metadata::text AS value
                FROM checkpoints
                WHERE thread_id = %s
                """,
                (str(thread.thread_id),),
            )
            checkpoint_text = "".join(
                value.decode() if isinstance(value, bytes) else value
                for value in (row["value"] for row in await checkpoint_cursor.fetchall())
            )
        assert blobs
        assert all(account_fixture.encode() not in blob for blob in blobs)
        assert all(b"hidden-checkpoint-reasoning" not in blob for blob in blobs)
        assert account_fixture not in checkpoint_text
        assert "hidden-checkpoint-reasoning" not in checkpoint_text
    finally:
        await storage.close()

    restarted = await open_storage(settings)
    try:
        await restarted.repository.mark_interrupted_runs()
        async with restarted.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT status, error_code FROM agent_runs WHERE run_id = %s",
                (interrupted_run,),
            )
            run_record = await cursor.fetchone()
            cursor = await connection.execute(
                "SELECT count(*) AS count FROM checkpoints WHERE thread_id = %s",
                (str(interrupted_run),),
            )
            abandoned_checkpoint_count = (await cursor.fetchone())["count"]
        assert run_record == {
            "status": "interrupted",
            "error_code": "agent_restarted",
        }
        assert abandoned_checkpoint_count == 0
        resumed = conversation_graph(restarted.checkpointer)
        snapshot = await resumed.aget_state({"configurable": {"thread_id": str(thread.thread_id)}})
        messages = snapshot.values["messages"]
        assert any(message.content == account_fixture for message in messages)
        assert any(
            isinstance(message, AIMessage)
            and message.additional_kwargs.get("reasoning_content") == "hidden-checkpoint-reasoning"
            for message in messages
        )
        await restarted.checkpointer.adelete_thread(str(thread.thread_id))
        assert await restarted.repository.delete_owned_thread(thread.thread_id, owner)
    finally:
        await restarted.close()
