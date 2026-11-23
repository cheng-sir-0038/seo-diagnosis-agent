import asyncio

import pytest
from sqlalchemy import func, select

from src.agent.harness.store import AgentChatHarnessStore, CreatedAgentTaskRun
from src.agent.models import AgentEvent, AgentRun, HumanInputAnswer, HumanInputState, RunStatus
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import SiteRow, WorkflowThreadRow


async def create_chat(
    store: AgentChatHarnessStore,
    conversation_id: str = "desk",
    *,
    site_id: str | None = None,
    state: dict | None = None,
) -> CreatedAgentTaskRun:
    return await store.create_task_run(
        conversation_id=conversation_id,
        site_id=site_id,
        title="课程页面",
        user_intent="核对课程页",
        messages=[{"role": "user", "content": "核对课程页"}],
        model_provider="test",
        model_name="test",
        dispatch_state=state,
    )


async def checkpoint(store: AgentChatHarnessStore, run: AgentRun, stage: str) -> dict:
    return await store.save_checkpoint(
        run,
        messages=run.messages,
        iteration=run.iteration,
        tool_count=run.tool_count,
        pending_tool_calls=run.pending_tool_calls,
        stage=stage,
        token_usage=run.token_usage,
    )


@pytest.mark.asyncio
async def test_site_free_chat_restores_its_route_and_reuses_only_completed_context(
    pg_url: str,
) -> None:
    database = SeoAgentDatabase(pg_url)
    await database.startup()
    store = AgentChatHarnessStore(database)
    try:
        draft = {"agent": "healthcare"}
        created = await create_chat(store, state=draft)
        run = await store.restore_run(created.run_id)
        assert run.site_id is None
        assert run.dispatch_state == draft
        async with database.orm_session() as session:
            assert await session.scalar(select(func.count()).select_from(SiteRow)) == 0
        assert await store.latest_dispatch_state("desk") == {}

        confirmed = {"agent": "seo", "target_url": "https://example.com/course"}
        run.dispatch_state = confirmed
        run.status = RunStatus.COMPLETED
        await checkpoint(store, run, "completed")
        await store.finalize_run(run)

        pending = await create_chat(store, state={"target_url": "https://other.example.com/"})
        assert created.thread_id == pending.thread_id
        assert created.run_id != pending.run_id
        assert await store.latest_dispatch_state("desk") == confirmed
        assert await store.latest_dispatch_state("other") == {}
    finally:
        await database.shutdown()

    reopened = SeoAgentDatabase(pg_url)
    await reopened.startup()
    try:
        reader = AgentChatHarnessStore(reopened)
        restored = await reader.restore_run(created.run_id, mark_recovering=False)
        assert restored.dispatch_state == confirmed
        assert restored.status == RunStatus.COMPLETED
        assert (await reader.get_run(created.run_id))["checkpoint"]["dispatch_state"] == confirmed
    finally:
        await reopened.shutdown()


@pytest.mark.asyncio
async def test_concurrent_site_free_runs_share_thread_and_failed_creation_rolls_back(
    pg_url: str,
) -> None:
    database = SeoAgentDatabase(pg_url)
    await database.startup()
    store = AgentChatHarnessStore(database)
    try:
        first, second = await asyncio.gather(create_chat(store), create_chat(store))
        assert first.thread_id == second.thread_id
        assert first.task_id != second.task_id
        with pytest.raises(TypeError, match="not JSON serializable"):
            await create_chat(store, conversation_id="rejected", state={"invalid": object()})
        assert len(await store.list_recoverable_runs()) == 2
        async with database.orm_session() as session:
            assert await session.scalar(select(func.count()).select_from(WorkflowThreadRow)) == 1
        assert len(await store.list_tasks()) == 2
        run = await store.restore_run(first.run_id, mark_recovering=False)
        run.dispatch_state = {"invalid": object()}
        with pytest.raises(TypeError, match="not JSON serializable"):
            await checkpoint(store, run, "dispatched")
        saved = (await store.get_run(run.id))["checkpoint"]
        assert (saved["stage"], saved["dispatch_state"]) == ("created", {})
        left = await store.restore_run(run.id, mark_recovering=False)
        right = await store.restore_run(run.id, mark_recovering=False)
        await asyncio.gather(checkpoint(store, left, "first"), checkpoint(store, right, "second"))
        assert (await store.get_run(run.id))["checkpoint"]["checkpoint_no"] == 3
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_site_free_input_receipt_retains_dispatch_state_on_replay(pg_url: str) -> None:
    database = SeoAgentDatabase(pg_url)
    await database.startup()
    store = AgentChatHarnessStore(database)
    try:
        route = {"agent": "healthcare", "next": "exam_policy"}
        created = await create_chat(store, state=route)
        run = await store.restore_run(created.run_id, mark_recovering=False)
        run.human_input = HumanInputState(
            request_id="exam-conditions",
            questions=["上一次在哪类基地考试？"],
            tool_name="exam_policy",
            tool_arguments={},
            tool_result={},
        )
        run.status = RunStatus.WAITING_FOR_INPUT
        await checkpoint(store, run, "waiting_for_input")

        answer = HumanInputAnswer(request_id="exam-conditions", answer="国家实践技能考试基地")
        receipts = await asyncio.gather(
            store.submit_input(run.id, answer), store.submit_input(run.id, answer)
        )
        assert sorted(replayed for _, replayed, _ in receipts) == [False, True]
        assert receipts[0][2] == receipts[1][2]
        duplicate = next(saved for saved, replayed, _ in receipts if replayed)
        assert duplicate.human_input and duplicate.human_input.answer == answer.answer
        assert duplicate.messages.count({"role": "user", "content": answer.answer}) == 1
        assert duplicate.dispatch_state == route
        with pytest.raises(ValueError, match="不能用同一请求替换回答"):
            await store.submit_input(run.id, answer.model_copy(update={"answer": "其他基地"}))
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_followup_loads_recent_server_dialog_without_tool_traffic(pg_url: str) -> None:
    database = SeoAgentDatabase(pg_url)
    await database.startup()
    store = AgentChatHarnessStore(database)
    try:
        route = {"route": "seo", "active_agent": "seo", "target_url": "https://example.com/"}
        created = await create_chat(store, state=route)
        run = await store.restore_run(created.run_id, mark_recovering=False)
        run.messages = [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "超出最近四条的问题"},
            {"role": "assistant", "content": "超出最近四条的答复"},
            {"role": "user", "content": "查标题"},
            {"role": "assistant", "content": "已核对标题"},
            {"role": "user", "content": "看看描述"},
            {"role": "assistant", "content": "过程文本", "tool_calls": [{"id": "step"}]},
            {"role": "tool", "content": "原始工具响应", "tool_call_id": "step"},
            {"role": "assistant", "content": "稿" * 2010},
        ]
        run.status = RunStatus.COMPLETED
        await checkpoint(store, run, "completed")
        await store.finalize_run(run)

        dialog = [
            {"role": "user", "content": "查标题"},
            {"role": "assistant", "content": "已核对标题"},
            {"role": "user", "content": "看看描述"},
            {"role": "assistant", "content": "稿" * 2000},
        ]
        pointer = {**route, "source_run_id": run.id}
        assert await store.latest_dispatch_state("desk", include_dialog=False) == pointer
        assert await store.latest_dispatch_state("desk") == {**pointer, "server_dialog": dialog}
        assert await store.completed_dialog(run.id, "desk", "seo") == dialog
        assert await store.completed_dialog(run.id, "desk", "healthcare") == []
        assert await store.completed_dialog(run.id, "another-chat", "seo") == []
        assert await store.completed_dialog("wrong-run", "desk", "seo") == []

        newer = await create_chat(store, state=route)
        assert await store.completed_dialog(newer.run_id, "desk", "seo") == []
        later_run = await store.restore_run(newer.run_id, mark_recovering=False)
        later_run.status = RunStatus.COMPLETED
        await checkpoint(store, later_run, "completed")
        assert (await store.latest_dispatch_state("desk", include_dialog=False))[
            "source_run_id"
        ] == newer.run_id
        assert await store.completed_dialog(run.id, "desk", "seo") == dialog
        assert (await store.get_run(run.id))["checkpoint"]["dispatch_state"] == route
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_only_one_retry_can_claim_a_failed_or_blocked_run(pg_url: str) -> None:
    database = SeoAgentDatabase(pg_url)
    await database.startup()
    store = AgentChatHarnessStore(database)
    try:
        created = await create_chat(store)
        run = await store.restore_run(created.run_id, mark_recovering=False)
        with pytest.raises(ValueError):
            await store.claim_retry(run.id)
        run.status = RunStatus.FAILED
        run.error = "模拟模型超时"
        await checkpoint(store, run, "failed")
        await store.finalize_run(run)
        outcomes = await asyncio.gather(
            store.claim_retry(run.id), store.claim_retry(run.id), return_exceptions=True
        )
        assert sum(isinstance(outcome, ValueError) for outcome in outcomes) == 1
        retry = next(outcome for outcome in outcomes if isinstance(outcome, AgentRun))
        assert retry.status == RunStatus.RECOVERING and retry.error is None
        assert (await store.get_run(run.id))["status"] == "RECOVERING"

        await store.mark_recovery_blocked(run.id, "测试依赖缺失")
        assert (await store.claim_retry(run.id)).status == RunStatus.RECOVERING
        for terminal in (RunStatus.COMPLETED, RunStatus.CANCELLED):
            run.status = terminal
            await checkpoint(store, run, terminal.value)
            await store.finalize_run(run)
            with pytest.raises(ValueError):
                await store.claim_retry(run.id)
            assert (await store.get_run(run.id))["status"] == terminal.value.upper()
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_wait_events_questions_and_checkpoint_survive_restart_together(pg_url: str) -> None:
    database = SeoAgentDatabase(pg_url)
    await database.startup()
    store = AgentChatHarnessStore(database)
    try:
        created = await create_chat(store)
        run = await store.restore_run(created.run_id, mark_recovering=False)
        run.status = RunStatus.RUNNING
        await checkpoint(store, run, "route_selected")
        question = "请补充页面地址。"
        run.human_input = HumanInputState(
            request_id="which-page",
            questions=[question],
            tool_name="workspace_clarification",
            tool_arguments={},
            tool_result={},
        )
        messages = [*run.messages, {"role": "assistant", "content": question}]
        events = (
            AgentEvent(
                run_id=run.id,
                type="message.completed",
                sequence=run.sequence + 1,
                payload={"content": question},
            ),
            AgentEvent(
                run_id=run.id,
                type="run.waiting_for_input",
                sequence=run.sequence + 2,
                payload={"request_id": "which-page", "questions": [question]},
            ),
        )
        run.dispatch_state = {"invalid": object()}
        with pytest.raises(TypeError):
            await store.save_waiting(run, messages=messages, events=events)
        before = await store.get_run(run.id)
        assert (before["status"], before["events"], before["checkpoint"]["stage"]) == (
            "RUNNING",
            [],
            "route_selected",
        )
        run.dispatch_state = {"route": "seo"}
        await store.save_waiting(run, messages=messages, events=events)
    finally:
        await database.shutdown()

    reopened = SeoAgentDatabase(pg_url)
    await reopened.startup()
    try:
        store = AgentChatHarnessStore(reopened)
        restored = await store.restore_run(created.run_id, mark_recovering=False)
        assert restored.status == RunStatus.WAITING_FOR_INPUT
        assert restored.messages == messages
        assert restored.events == list(events)
        assert restored.human_input is not None
        assert restored.human_input.request_id == restored.events[-1].payload["request_id"]
        answer = HumanInputAnswer(request_id=restored.human_input.request_id, answer="example.com")
        resumed, replayed, sequence = await store.submit_input(restored.id, answer)
        assert not replayed and sequence == events[-1].sequence + 1
        assert resumed.dispatch_state == {"route": "seo"}
    finally:
        await reopened.shutdown()
