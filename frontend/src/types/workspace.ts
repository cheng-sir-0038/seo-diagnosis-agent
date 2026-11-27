export interface DocumentSummary {
  id: string
  name: string
  size_bytes: number
  text_chars: number
  uploaded_at: string
  in_library: boolean
  excerpt?: { location: string, text: string }
}

export interface KnowledgeReference {
  title: string
  source_url: string
  section: string
  quote: string
}

export interface WorkspaceLibrary {
  documents: DocumentSummary[]
  references: KnowledgeReference[]
}

export interface SourceField {
  id: string
  label: string
  kind: 'secret' | 'text' | 'switch' | 'choice'
  required: boolean
  editable: boolean
  configured: boolean
  options?: { value: string, label: string }[]
  value?: string
}

export interface TextModelStatus {
  provider: 'openai' | 'deepseek'
  model: string | null
  configured: boolean
}

export interface WorkspaceSource {
  id: string
  name: string
  group: string
  description: string
  mode: string
  restart_required: boolean
  fields: SourceField[]
  text_model?: TextModelStatus | null
  pending_text_model?: TextModelStatus | null
}
