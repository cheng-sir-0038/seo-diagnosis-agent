import { describe, expect, it } from 'vitest'

import setupPage from '../pages/SetupPage.tsx?raw'

describe('setup layout scroll contract', () => {
  it('keeps the primary action outside the scrolling form fields', () => {
    expect(setupPage).toContain('className="setup-form-scroll"')
    expect(setupPage).toContain('className="setup-actions"')
    expect(setupPage.indexOf('className="setup-actions"')).toBeGreaterThan(
      setupPage.indexOf('className="setup-form-scroll"'),
    )
  })

  it('uses controlled country codes and BCP47 language options', () => {
    expect(setupPage).toContain("{ value: 'US', label: '美国（US）' }")
    expect(setupPage).toContain("{ value: 'zh-CN', label: '简体中文 · zh-CN' }")
    expect(setupPage).toContain("{ value: 'zh-TW', label: '繁体中文 · zh-TW' }")
    expect(setupPage).toContain("{ value: 'ja-JP', label: '日语 · ja-JP' }")
    expect(setupPage).toContain("language: 'en-US'")
  })
})
