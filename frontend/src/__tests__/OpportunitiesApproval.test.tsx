import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { OpportunitiesPage } from '../pages/OpportunitiesPage'

const api = vi.hoisted(() => ({
  listOpportunities: vi.fn(),
  getOpportunity: vi.fn(),
  createTask: vi.fn(),
}))

vi.mock('../context/ProductContext', () => ({
  useProduct: () => ({ config: { siteId: 'site-1', ownerActorId: 'owner-1' } }),
}))
vi.mock('../services/productApi', () => ({ productApi: api }))

beforeEach(() => {
  const opportunity = {
    id: 'opp-1', title: '示例机会', normalized_intent: 'example', current_score: 90,
    priority_level: 'HIGH', status: 'APPROVED', last_seen_at: '2026-01-01T00:00:00Z',
  }
  api.listOpportunities.mockResolvedValue([opportunity])
  api.getOpportunity.mockResolvedValue(opportunity)
  api.createTask.mockRejectedValue(new Error('关联机会尚未通过审批'))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it('shows the approval error when task creation is rejected', async () => {
  render(<OpportunitiesPage />)
  fireEvent.click(await screen.findByText('示例机会'))
  fireEvent.click(await screen.findByRole('button', { name: /创建执行任务/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'OK' }))

  await waitFor(() => expect(api.createTask).toHaveBeenCalledTimes(1))
  expect(await screen.findByText('关联机会尚未通过审批')).toBeInTheDocument()
  expect(screen.getByText('创建内部任务')).toBeInTheDocument()
})

it('prevents task creation for an opportunity awaiting approval', async () => {
  const opportunity = {
    id: 'opp-2', title: '待审批机会', normalized_intent: 'example', current_score: 90,
    priority_level: 'HIGH', status: 'WAITING_FOR_DECISION', last_seen_at: '2026-01-01T00:00:00Z',
  }
  api.listOpportunities.mockResolvedValue([opportunity])
  api.getOpportunity.mockResolvedValue(opportunity)
  render(<OpportunitiesPage />)
  fireEvent.click(await screen.findByText('待审批机会'))

  expect(await screen.findByRole('button', { name: /创建执行任务/ })).toBeDisabled()
  expect(screen.getByText('关联机会尚未通过审批，暂不能创建任务。')).toBeInTheDocument()
  expect(api.createTask).not.toHaveBeenCalled()
})
