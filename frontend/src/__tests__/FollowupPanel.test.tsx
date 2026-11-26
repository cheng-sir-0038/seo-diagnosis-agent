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
const issue = { item: '示例项目', text: '请核对两条原记录：方法缺失或不同', source: `${before.source} / ${after.source}` }

function renderCase(overrides: Partial<api.FollowupCase> = {}) {
  vi.spyOn(api, 'followupRequest').mockResolvedValue({
    subject_id: 'CASE-01', demo: false, revision: 1, files: [], records: [before, after],
    report_review: { comparable_pairs: 0, issues: [issue] }, draft: null, saved: null,
    ...overrides,
  })
  const onContinue = vi.fn()
  render(<FollowupPanel runId="test-run" access="test-access" canContinue onContinue={onContinue} />)
  return onContinue
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it('keeps the existing-records option available for two incompatible report dates', async () => {
  const onContinue = renderCase()
  const available = await screen.findByRole('button', { name: '按现有资料整理' })
  expect(screen.getByText(issue.text)).toBeVisible()
  expect(screen.getByText(issue.source)).toBeVisible()
  expect(screen.queryByRole('button', { name: '整理并核对资料' })).not.toBeInTheDocument()
  fireEvent.click(available)
  expect(onContinue).toHaveBeenCalledWith(api.followupActions.available)
})

it('attributes comparison fields and review context to their source rows', async () => {
  const corrected = { ...after, original: after.record, record: { ...after.record, method: before.record.method } }
  const purchase: api.Evidence = {
    id: 'purchase', source: 'purchase.csv · 第 2 行', original: null,
    record: { ...before.record, kind: 'purchase', item: '示例药盒', note: '购药单备注' },
  }
  renderCase({
    records: [before, corrected, purchase], report_review: { comparable_pairs: 1, issues: [] },
    draft: {
      comparisons: [{ item: '示例项目', earlier: before.id, later: after.id, delta: '-0.5', unit: 'U', reasons: [] }],
      issues: [], plans: [], pending_plans: [],
    },
  })
  const comparison = within(await screen.findByRole('region', { name: '报告对照' }))
  expect(comparison.getByText('后次 − 前次 = -0.5 U')).toBeVisible()
  expect(comparison.getByText(before.source)).toBeVisible()
  expect(comparison.getByText(after.source)).toBeVisible()
  expect(comparison.getByText('含人工修订。修订前 方法 方法 B')).toBeVisible()
  expect(comparison.getAllByText('原报告参考范围 1–6')).toHaveLength(2)

  const review = within(screen.getByRole('region', { name: '复诊准备清单' }))
  const purchaseDetails = review.getByText('供复诊核对的日常与用药资料（1 条）').closest('details')!
  purchaseDetails.open = true
  expect(review.getByText('购药记录仅用于核对购买情况。当前使用情况需另行确认。')).toBeVisible()
  expect(review.getByText(purchase.source)).toBeVisible()
})
