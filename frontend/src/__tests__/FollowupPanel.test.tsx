import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import FollowupPanel from '../components/FollowupPanel'
import * as api from '../services/followupApi'

const before: api.Evidence = {
  id: 'before', source: 'first.csv · 第 2 行', original: null,
  record: {
    kind: 'report', item: '示例项目', record_date: '2026-10-01', value: '5.9', unit: 'U',
    specimen: '标本 A', method: '方法 A', reference_range: '1–6', flag: '原文件标记 A',
    note: null, due_date: null, plan_status: null,
  },
}
const after: api.Evidence = {
  ...before, id: 'after', source: 'second.csv · 第 2 行',
  record: { ...before.record, record_date: '2026-10-14', value: '5.4', method: '方法 B' },
}
const empty: api.FollowupCase = {
  subject_id: null, demo: false, revision: 0, files: [], records: [],
  report_review: { comparable_pairs: 0, issues: [] }, draft: null, saved: null,
}
const uploaded = {
  ...empty, subject_id: 'CASE-01', revision: 3, records: [before],
  files: [{ name: 'first.csv', digest: 'file-1', uploaded_at: '2026-10-01', rows: 1, error: null }],
}

function renderCase(data: api.FollowupCase = empty, canContinue = true) {
  const request = vi.spyOn(api, 'followupRequest').mockResolvedValue(data)
  const onContinue = vi.fn()
  render(<FollowupPanel runId="test-run" access="test-access" canContinue={canContinue} onContinue={onContinue} />)
  return { onContinue, request }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it('offers both importing and loading the demo from the empty case', async () => {
  const { request } = renderCase()
  const importButton = await screen.findByRole('button', { name: '导入资料' })
  expect(importButton).toBeDisabled()
  fireEvent.change(screen.getByRole('textbox', { name: '匿名对象编号' }), { target: { value: 'CASE-01' } })
  expect(importButton).toBeEnabled()
  expect(screen.queryByRole('button', { name: '整理复诊前准备清单' })).not.toBeInTheDocument()
  request.mockResolvedValueOnce({ ...uploaded, demo: true })
  fireEvent.click(screen.getByRole('button', { name: '载入合成样例' }))
  expect(request).toHaveBeenLastCalledWith('test-run', 'test-access', '/demo-materials', { method: 'POST' })
  expect(await screen.findByRole('button', { name: '整理复诊前准备清单' })).toBeEnabled()
})

it('uses a single file to prepare the checklist with no per-record write action', async () => {
  const { onContinue } = renderCase(uploaded)
  const panel = within(await screen.findByRole('region', { name: '复诊资料区' }))
  fireEvent.click(panel.getByRole('button', { name: '整理复诊前准备清单' }))
  expect(onContinue).toHaveBeenCalledWith(api.followupActions.prepare)
  panel.getByText('资料记录（1 条）').closest('details')!.open = true
  expect(panel.getByText(before.source)).toBeVisible()
  expect(panel.queryByRole('button', { name: /修订|核对/ })).not.toBeInTheDocument()
})

it('presents source evidence next to checklist items and confirms the visible revision', async () => {
  const { onContinue } = renderCase({
    ...uploaded,
    draft: {
      revision: 3, comparisons: [], issues: [], plans: [], pending_plans: [],
      checklist: {
        materials_to_bring: [{ id: 'file-1', title: 'first.csv', sources: [{ record_id: before.id, source: before.source }] }],
        existing_arrangements: [],
        questions_for_doctor: [{ id: 'question-1', title: '复诊时需要带哪份原报告？', sources: [{ record_id: before.id, source: before.source }] }],
      },
    },
  })
  const materials = within(await screen.findByRole('region', { name: '要带的资料' }))
  materials.getByText('查看来源（1 行）').closest('details')!.open = true
  expect(materials.getByText(before.source)).toBeVisible()
  expect(materials.getByText(/标本 A/)).toBeVisible()
  expect(screen.getByText(/当前资料没有预约记录/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '确认保存资料包' }))
  expect(onContinue).toHaveBeenCalledWith(api.followupActions.confirm, 3)
})

it('keeps saved legacy packages and their report comparison provenance readable', async () => {
  const corrected = { ...after, original: after.record, record: { ...after.record, method: before.record.method } }
  renderCase({
    ...uploaded, records: [before, corrected],
    draft: {
      comparisons: [{ item: '示例项目', earlier: before.id, later: after.id, delta: '-0.5', unit: 'U', reasons: [] }],
      issues: [], plans: [], pending_plans: [],
    },
    saved: { saved_at: '2026-10-01', tasks: [] },
  }, false)
  const legacy = (await screen.findByText('查看旧版资料包')).closest('details')!
  legacy.open = true
  const compare = within(legacy).getByText('报告原值与比较依据').closest('details')!
  compare.open = true
  expect(within(compare).getByText('后次 − 前次 = -0.5 U')).toBeVisible()
  expect(within(compare).getByText(after.source)).toBeVisible()
  expect(within(compare).getByText('导入时的记录：方法 方法 B')).toBeVisible()
  expect(within(compare).getAllByText('原报告参考范围 1–6')).toHaveLength(2)
  expect(screen.getByText('已保存资料包')).toBeVisible()
  expect(screen.queryByRole('button', { name: '确认保存资料包' })).not.toBeInTheDocument()
})
