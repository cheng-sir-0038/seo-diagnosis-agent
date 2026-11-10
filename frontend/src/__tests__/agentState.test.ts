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

})
