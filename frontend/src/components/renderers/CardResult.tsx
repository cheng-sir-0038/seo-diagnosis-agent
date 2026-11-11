import type { CardResultData } from './contracts'

interface CardResultProps {
  data: CardResultData
}

const pageReviewLabels: Record<string, string> = {
  baseline_recorded: '页面基线已保存',
  passed: '与目标一致',
  failed: '与目标不一致',
  insufficient_evidence: '证据不足，暂不能判断',
  title: '页面标题',
  meta_description: '页面描述',
  h1s: 'H1 标题',
  canonical_url: 'Canonical 地址',
}

export function CardResult({ data }: CardResultProps) {
  const displayText = (value: string) => data.capability === 'page_change_review'
    ? pageReviewLabels[value] ?? value
    : value
  return (
    <section className={`tool-card-result${data.variant ? ` ${data.variant}` : ''}`} data-testid="card-result">
      {data.items.map((item) => (
        <article className={`tool-card-item${item.image_url ? '' : ' no-image'}`} key={item.key}>
          {item.image_url ? (
            <img src={item.image_url} alt={item.title} loading="lazy" />
          ) : null}
          <div className="tool-card-body">
            <h4>
              {item.url ? (
                <a href={item.url} target="_blank" rel="noreferrer">{displayText(item.title)}</a>
              ) : displayText(item.title)}
            </h4>
            {item.summary ? <p>{displayText(item.summary)}</p> : null}
            {item.code ? <pre className="tool-card-code"><code>{item.code}</code></pre> : null}
            {item.metadata?.length ? (
              <dl>
                {item.metadata.map((entry) => (
                  <div key={`${item.key}-${entry.label}`}>
                    <dt>{entry.label}</dt>
                    <dd>{entry.value}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
          </div>
        </article>
      ))}
    </section>
  )
}
