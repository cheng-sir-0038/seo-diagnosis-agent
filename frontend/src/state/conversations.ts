import type { Conversation } from '../types/agent'

const STORAGE_KEY = 'agent-workspace-conversations-v2'
const ACTIVE_CONVERSATION_KEY = 'agent-workspace-active-conversation-v2'

export function clearLegacyWorkspaceStorage(): void {
  const legacyKeys: [Storage, string][] = [
    [localStorage, 'agent-workspace-conversations-v1'],
    [localStorage, 'seo-agent-product-config-v1'],
    [sessionStorage, 'agent-workspace-active-conversation-v1'],
  ]
  for (const [storage, key] of legacyKeys) {
    if (storage.getItem(key) !== null) storage.removeItem(key)
  }
}

export const createConversation = (): Conversation => ({
  id: crypto.randomUUID(),
  title: '新会话',
  messages: [],
  updatedAt: new Date().toISOString(),
})

export function loadConversations(): Conversation[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]') as Conversation[]
    return parsed.length ? parsed : [createConversation()]
  } catch {
    return [createConversation()]
  }
}

export function saveConversations(conversations: Conversation[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations))
}

export function loadActiveConversationId(conversations: Conversation[]): string {
  const saved = sessionStorage.getItem(ACTIVE_CONVERSATION_KEY)
  return conversations.find(({ id }) => id === saved)?.id ?? conversations[0].id
}

export function saveActiveConversationId(conversationId: string): void {
  sessionStorage.setItem(ACTIVE_CONVERSATION_KEY, conversationId)
}
