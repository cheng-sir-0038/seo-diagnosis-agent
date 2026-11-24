import { workspaceHeaders } from '../state/workspaceAccess'
import type { DocumentSummary, WorkspaceLibrary, WorkspaceSource } from '../types/workspace'
import { agentApiUrl } from './agentApi'

const DOCUMENTS = '/api/v1/workspace/documents'
const SOURCES = '/api/v1/workspace/sources'
export const WORKSPACE_FILE_ACCEPT = '.txt,.md,.csv,.json,.pdf'
export const MAX_DOCUMENTS_PER_TURN = 4

async function workspaceRequest(path: string, init: RequestInit = {}) {
  const response = await fetch(agentApiUrl(path), { ...init, cache: 'no-store' })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    if (response.status === 413) throw new Error('单个文件最多 1 MB。')
    throw new Error(typeof data.detail === 'string' ? data.detail : `操作失败（${response.status}）`)
  }
  return response
}

export async function uploadDocument(file: File, conversationId?: string): Promise<DocumentSummary> {
  if (file.size > 1024 * 1024) throw new Error('单个文件最多 1 MB。')
  const query = conversationId ? `?${new URLSearchParams({ conversation_id: conversationId })}` : ''
  const response = await workspaceRequest(DOCUMENTS + query, {
    method: 'POST',
    headers: { ...workspaceHeaders(true), 'X-Workspace-File': encodeURIComponent(file.name), 'Content-Type': 'application/octet-stream' },
    body: file,
  })
  return response.json()
}

export async function listLibrary(query: string, references: boolean, signal?: AbortSignal): Promise<WorkspaceLibrary> {
  const params = new URLSearchParams({ q: query, include_references: String(references) })
  const response = await workspaceRequest(`${DOCUMENTS}?${params}`, { headers: workspaceHeaders(true), signal })
  return response.json()
}

export async function downloadDocument(document: DocumentSummary) {
  const response = await workspaceRequest(`${DOCUMENTS}/${document.id}/download`, { headers: workspaceHeaders() })
  const href = URL.createObjectURL(await response.blob())
  const link = window.document.createElement('a')
  link.href = href
  link.download = document.name
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(href), 1000)
}

export async function deleteLibraryDocument(id: string) {
  await workspaceRequest(`${DOCUMENTS}/${id}`, { method: 'DELETE', headers: workspaceHeaders() })
}

export async function listWorkspaceSources(signal?: AbortSignal): Promise<WorkspaceSource[]> {
  const response = await workspaceRequest(SOURCES, { signal })
  return response.json()
}

export async function saveWorkspaceSource(id: string, fields: Record<string, string | boolean>): Promise<WorkspaceSource[]> {
  const response = await workspaceRequest(`${SOURCES}/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  })
  return response.json()
}
