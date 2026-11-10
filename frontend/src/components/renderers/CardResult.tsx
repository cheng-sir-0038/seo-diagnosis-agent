import type { CardResultData } from './contracts'

interface CardResultProps {
  data: CardResultData
}

export function CardResult({ data }: CardResultProps) {
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
                <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a>
              ) : item.title}
            </h4>
            {item.summary ? <p>{item.summary}</p> : null}
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
