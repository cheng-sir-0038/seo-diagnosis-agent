import type { Conversation } from '../types/agent'

const STORAGE_KEY = 'agent-workspace-conversations-v1'

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
