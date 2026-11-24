import type { ChatMessage, Conversation } from '../types/agent'

const labels: Record<string, [string, string]> = {
  PENDING: ['准备中', 'running'], RUNNING: ['进行中', 'running'], RECOVERING: ['恢复中', 'running'],
  WAITING_FOR_INPUT: ['等待补充', 'waiting'], RECOVERY_BLOCKED: ['恢复受阻', 'error'],
  COMPLETED: ['已完成', 'success'], PARTIAL: ['部分完成', 'waiting'], FAILED: ['执行失败', 'error'],
  CANCELLED: ['已停止', 'neutral'],
}

export function taskStatus(status?: string, partial?: boolean) {
  const key = status?.toUpperCase()
  return labels[key === 'COMPLETED' && partial ? 'PARTIAL' : key ?? ''] ?? ['状态待同步', 'neutral']
}

export function replyStatus(reply?: ChatMessage) {
  return reply?.runStatus ?? (reply?.inputRequest ? 'waiting_for_input' : undefined)
}

export function conversationStatus(conversation: Conversation) {
  const last = conversation.messages.at(-1)
  if (conversation.activeRun && last?.runId !== conversation.activeRun.runId) return 'RUNNING'
  return last?.partial && replyStatus(last) === 'completed' ? 'PARTIAL' : replyStatus(last)?.toUpperCase()
}
