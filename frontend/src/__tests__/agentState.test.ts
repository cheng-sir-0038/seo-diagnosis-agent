import { describe, expect, it } from 'vitest'

import { initialRunState, reduceAgentEvent } from '../state/agentState'
import type { AgentEvent } from '../types/agent'

const event = (
  sequence: number,
  type: AgentEvent['type'],
  payload: Record<string, unknown>,
): AgentEvent => ({
  event_id: `evt_${sequence}`,
  run_id: 'run_1',
  type,
  sequence,
  created_at: '2026-07-23T12:00:00Z',
  payload,
})

describe('reduceAgentEvent', () => {
  it('appends deltas and ignores duplicate sequence', () => {
    const first = reduceAgentEvent(initialRunState, event(1, 'message.delta', { delta: '你' }))
    const second = reduceAgentEvent(first, event(2, 'message.delta', { delta: '好' }))
    const duplicate = reduceAgentEvent(second, event(2, 'message.delta', { delta: '重复' }))

    expect(second.assistantText).toBe('你好')
    expect(duplicate.assistantText).toBe('你好')
  })

  it('tracks tool lifecycle, render hint and terminal status', () => {
    const started = reduceAgentEvent(initialRunState, event(1, 'tool.started', {
      tool_call_id: 'c1',
      name: 'calculator',
      arguments: { expression: '1+2' },
    }))
    const completed = reduceAgentEvent(started, event(2, 'tool.completed', {
      tool_call_id: 'c1',
      name: 'calculator',
      result: { success: true, data: { result: 3 } },
      render_hint: 'table',
    }))
    const done = reduceAgentEvent(completed, event(3, 'run.completed', { status: 'completed' }))

    expect(done.tools[0].status).toBe('completed')
    expect(done.tools[0].render_hint).toBe('table')
    expect(done.status).toBe('completed')
  })

  it('ignores unknown render hints', () => {
    const completed = reduceAgentEvent(initialRunState, event(1, 'tool.completed', {
      tool_call_id: 'c1',
      name: 'tool',
      result: { ok: true },
      render_hint: 'iframe',
    }))

    expect(completed.tools[0].render_hint).toBeUndefined()
  })

  it('accepts new specialist IDs in dispatch order and rejects reserved or malformed IDs', () => {
    const first = event(1, 'agent.dispatched', { agent: 'healthcare', agent_run_id: 'medical' })
    const second = event(2, 'agent.dispatched', { agent: 'seo', agent_run_id: 'page' })
    const state = [
      first, second, second,
      event(3, 'agent.dispatched', { agent: 'archive_review', agent_run_id: 'archive' }),
      ...['supervisor', 'clarify', 'course_review', '__start__', 'Archive', 'a'.repeat(65)].map((agent, index) =>
        event(4 + index, 'agent.dispatched', { agent, agent_run_id: agent })),
    ].reduce(reduceAgentEvent, initialRunState)
    expect(state.agentRuns.map(run => run.agent)).toEqual(['healthcare', 'seo', 'archive_review'])
    expect(reduceAgentEvent(state, event(0, 'run.reset', { status: 'pending' })).agentRuns).toEqual([])
  })

  it('attaches a late specialist profile and preserves it in continuation state', () => {
    const archive = { agent: 'archive_review', agent_run_id: 'archive' }
    const profile = { name: '档案核验', icon: 'search', tone: 'indigo', directory_tone: 'learning' }
    const state = [
      event(1, 'agent.started', archive),
      event(2, 'skill.loaded', { ...archive, skill: 'archive-scan', label: '核对档案', agent_profile: profile }),
      event(3, 'tool.completed', { ...archive, tool_call_id: 'read', name: 'archive_read' }),
      event(4, 'agent.completed', { ...archive, agent_profile: { name: 1 } }),
    ].reduce(reduceAgentEvent, initialRunState)
    const [run] = state.agentRuns
    expect(run).toMatchObject({ agent: archive.agent, profile, status: 'completed' })
    expect(state.skills[0]).toMatchObject({ agentRunId: run.id, name: 'archive-scan' })
    expect(state.tools[0]).toMatchObject({ agentRunId: run.id, agent: archive.agent })
    const resumed = reduceAgentEvent(state, event(0, 'run.reset', { priorAgentRuns: [run], priorSkills: state.skills }))
    expect(resumed.agentRuns).toEqual([run])
    expect(resumed.skills).toEqual(state.skills)
  })

  it('attributes interleaved calls by dispatch ID, including identical tool call IDs', () => {
    const medical = { agent: 'healthcare', agent_run_id: 'medical' }
    const seo = { agent: 'seo', agent_run_id: 'page' }
    const state = [
      event(1, 'agent.dispatched', medical),
      event(2, 'agent.dispatched', seo),
      event(3, 'tool.started', { ...medical, tool_call_id: 'call', name: 'medical_exam_syllabus' }),
      event(4, 'tool.completed', { ...seo, tool_call_id: 'call', name: 'seo_page_metadata' }),
      event(5, 'tool.failed', {
        ...medical, tool_call_id: 'call', name: 'medical_exam_syllabus',
        result: { success: false, error: '资料暂时无法读取' },
      }),
      event(6, 'tool.completed', { tool_call_id: 'unassigned', name: 'other_tool' }),
    ].reduce(reduceAgentEvent, initialRunState)
    expect(state.tools.map(tool => [tool.agent, tool.agentRunId, tool.status])).toEqual([
      ['healthcare', 'medical', 'failed'], ['seo', 'page', 'completed'], [undefined, undefined, 'completed'],
    ])
    expect(state.tools[0].error).toBe('资料暂时无法读取')
  })

  it('tracks waiting, reopening and failure independently for dispatches of the same specialist', () => {
    const first = { agent: 'seo', agent_run_id: 'page' }
    const second = { agent: 'seo', agent_run_id: 'another-page' }
    const waiting = [
      event(1, 'agent.dispatched', first),
      event(2, 'agent.dispatched', second),
      event(3, 'agent.completed', second),
      event(4, 'run.waiting_for_input', { ...first, request_id: 'request', questions: ['是哪一页？'] }),
    ].reduce(reduceAgentEvent, initialRunState)
    expect(waiting.agentRuns.map(run => run.status)).toEqual(['waiting_for_input', 'completed'])
    const failed = [
      event(5, 'run.resumed', {}),
      event(6, 'agent.dispatched', first),
      event(7, 'agent.failed', first),
      event(6, 'agent.dispatched', first),
      event(8, 'run.failed', { message: '暂时无法读取网页' }),
    ].reduce(reduceAgentEvent, waiting)
    expect(failed.agentRuns.map(run => [run.id, run.status])).toEqual([
      ['page', 'failed'], ['another-page', 'completed'],
    ])
    expect(failed.agentRuns[0].startedAt).toBe(waiting.agentRuns[0].startedAt)
  })

  it('marks budget completion as a partial successful result', () => {
    const done = reduceAgentEvent(initialRunState, event(1, 'run.completed', {
      status: 'completed',
      partial: true,
      completion_mode: 'budget_limit',
    }))

    expect(done.status).toBe('completed')
    expect(done.stage).toBe('已基于现有证据完成')
    expect(done.partial).toBe(true)
    expect(done.completionMode).toBe('budget_limit')
    expect(done.error).toBeUndefined()
  })

  it('stops open specialist and tool rows when the run is cancelled', () => {
    const seo = { agent: 'seo', agent_run_id: 'page' }
    const stopped = [
      event(1, 'agent.dispatched', seo),
      event(2, 'tool.started', { ...seo, tool_call_id: 'read', name: 'seo_page_metadata' }),
      event(3, 'run.cancelled', {}),
    ].reduce(reduceAgentEvent, initialRunState)
    expect(stopped.agentRuns[0].status).toBe('cancelled')
    expect(stopped.tools[0].status).toBe('cancelled')
  })

})
