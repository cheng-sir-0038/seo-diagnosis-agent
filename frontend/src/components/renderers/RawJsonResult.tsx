interface RawJsonResultProps {
  data: unknown
}

export function RawJsonResult({ data }: RawJsonResultProps) {
  return <pre className="tool-result-raw" data-testid="raw-tool-result">{JSON.stringify(data, null, 2)}</pre>
}
