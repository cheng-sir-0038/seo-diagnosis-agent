import { afterEach, describe, expect, it, vi } from 'vitest'

import { productApi } from '../services/productApi'

describe('automation product API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('calls the real structured PRD generation endpoint', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ id: 'prd-1', version_no: 1 }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await productApi.generateTaskPrd('task-1', 'owner-1')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/seo-agent/automation/tasks/task-1/generate-prd',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ actor_id: 'owner-1' }) }),
    )
  })
})
