import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ObservabilityPage } from '../pages/ObservabilityPage'
import { PromptOpsPage } from '../pages/PromptOpsPage'

const json = (data: unknown) =>
  Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } }))

function mockProductFetch() {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/observability/traces')) return json([])
    if (url.includes('/observability/issues')) return json([])
    if (url.includes('/observability/metrics')) return json([])
    if (url.includes('/promptops/prompts')) return json([])
    if (url.includes('/promptops/datasets')) return json([])
    if (url.includes('/promptops/evaluations')) return json([])
    if (url.includes('/promptops/experiments')) return json([])
    return json([])
  })
}

describe('Observability and PromptOps product pages', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('loads the observability page and requests traces', async () => {
    const fetchMock = mockProductFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<ObservabilityPage />)
    expect(await screen.findByRole('heading', { name: '运行观测' })).toBeInTheDocument()
    expect(await screen.findByText('暂无 Trace')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/observability/traces'), expect.anything()))
  })

  it('loads the PromptOps page and shows the empty state', async () => {
    const fetchMock = mockProductFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<PromptOpsPage />)
    expect(await screen.findByRole('heading', { name: '提示词实验' })).toBeInTheDocument()
    expect(await screen.findByText('暂无 Prompt')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/promptops/prompts'), expect.anything()))
  })
})
