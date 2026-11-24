const WORKSPACE_KEY = 'agent-workspace-access-v1'

export function workspaceHeaders(create = false): Record<string, string> {
  let key = localStorage.getItem(WORKSPACE_KEY)
  if (!key && create) {
    key = crypto.randomUUID()
    localStorage.setItem(WORKSPACE_KEY, key)
  }
  return key ? { 'X-Workspace-Key': key } : {}
}
