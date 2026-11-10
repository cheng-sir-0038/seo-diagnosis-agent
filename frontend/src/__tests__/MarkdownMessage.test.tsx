import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { MarkdownMessage } from '../components/MarkdownMessage'

afterEach(cleanup)

describe('MarkdownMessage', () => {
  it('converts a business ASCII chart into an accessible data summary', () => {
    render(
      <MarkdownMessage
        content={`### 三、排名分布趋势总结

\`\`\`text
排名分布变化趋势（近1个月）：

Top 3（第1-3名）      ███████ 64 个 → 大多稳定，品牌词护城河牢固
Top 4-10（第4-10名）  ██ 21 个 → 有 1 个从第 6 冲进第 1
排名 11+              █████████████ 129 个 → 仍有提升空间
\`\`\`

趋势判断：核心词稳定。`}
      />,
    )

    const summary = screen.getByRole('region', { name: '排名分布变化趋势（近1个月）' })
    expect(summary).toHaveAttribute('data-testid', 'markdown-data-summary')
    expect(within(summary).getByText('Top 3（第1-3名）')).toBeInTheDocument()
    expect(within(summary).getByText('64 个')).toBeInTheDocument()
    expect(within(summary).getByText('29.9%')).toBeInTheDocument()
    expect(within(summary).getByText(/品牌词护城河牢固/)).toBeInTheDocument()
    expect(screen.queryByText(/███████/)).not.toBeInTheDocument()
    expect(document.querySelector('.markdown-body pre')).toBeNull()
  })

  it('keeps real programming code as a normal code block', () => {
    render(
      <MarkdownMessage
        content={'```ts\nconst answer: number = 42\nconsole.log(answer)\n```'}
      />,
    )

    expect(document.querySelector('.markdown-body pre')).not.toBeNull()
    expect(screen.getByText(/const answer/)).toBeInTheDocument()
    expect(screen.queryByTestId('markdown-data-summary')).not.toBeInTheDocument()
  })

  it('converts an unfinished streaming ASCII block after enough rows arrive', () => {
    render(
      <MarkdownMessage
        content={`排名分布：

\`\`\`
Top 3     █████ 30 个 → 稳定
排名 11+  █████████ 70 个 → 待提升`}
      />,
    )

    expect(screen.getByTestId('markdown-data-summary')).toBeInTheDocument()
    expect(screen.getByText('30.0%')).toBeInTheDocument()
    expect(document.querySelector('.markdown-body pre')).toBeNull()
  })

  it('does not invent percentages for metrics with different business meanings', () => {
    render(
      <MarkdownMessage
        content={`\`\`\`text
流量与关键词覆盖：
流量       █████████ 52000 → 预估访问
关键词数   ███ 214 → 有排名词
Top 3      ██ 43 → 强势词
\`\`\``}
      />,
    )

    const summary = screen.getByTestId('markdown-data-summary')
    expect(within(summary).getByText('52000')).toBeInTheDocument()
    expect(within(summary).getByText('214')).toBeInTheDocument()
    expect(within(summary).getByText('43')).toBeInTheDocument()
    expect(within(summary).queryByText(/%$/)).not.toBeInTheDocument()
  })


  it('keeps explicit percentage text while clamping visual bars', () => {
    render(
      <MarkdownMessage
        content={`\`\`\`text
流量占比：
来源 A  ███████ 150% → 超过基准
来源 B  ██ 0% → 暂无贡献
\`\`\``}
      />,
    )

    const summary = screen.getByTestId('markdown-data-summary')
    expect(within(summary).getByText('150.0%')).toBeInTheDocument()
    expect(within(summary).getByText('0.0%')).toBeInTheDocument()
    const bars = summary.querySelectorAll<HTMLElement>('.markdown-data-summary-bar > span')
    expect(bars[0]).toHaveStyle({ width: '100%', minWidth: '3px' })
    expect(bars[1]).toHaveStyle({ width: '0%', minWidth: '0px' })
  })

})

it('hides persisted DSML tool-control markup from historical assistant messages', () => {
  render(
    <MarkdownMessage
      content={`检测到重复工具调用，已停止继续补查；以下结论基于已经获得的证据。

好的，我继续查关键词。

<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="seo_keyword_overview">
<｜｜DSML｜｜parameter name="keyword">5G smartphone</｜｜DSML｜｜parameter>
</｜｜DSML｜｜invoke>
</｜｜DSML｜｜tool_calls>`}
    />,
  )

  expect(screen.queryByText(/DSML/)).not.toBeInTheDocument()
  expect(screen.queryByText(/seo_keyword_overview/)).not.toBeInTheDocument()
  expect(screen.queryByText(/5G smartphone/)).not.toBeInTheDocument()
  expect(screen.getByText(/内部工具控制内容已隐藏/)).toBeInTheDocument()
})
