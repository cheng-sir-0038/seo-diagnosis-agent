import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from '../pages/ChatPage'

describe('Chat page effects', () => {
  const originalScrollIntoView = Element.prototype.scrollIntoView

  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    Element.prototype.scrollIntoView = originalScrollIntoView
    vi.restoreAllMocks()
  })

  it('does not return the scrollIntoView result as an effect cleanup', async () => {
    Element.prototype.scrollIntoView = vi.fn(() => Promise.resolve()) as unknown as (
      arg?: boolean | ScrollIntoViewOptions,
    ) => void
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    render(<ChatPage />)
    await Promise.resolve()

    expect(
      consoleError.mock.calls.some(([message]) =>
        String(message).includes('useEffect must not return anything besides a function'),
      ),
    ).toBe(false)
  })
})
