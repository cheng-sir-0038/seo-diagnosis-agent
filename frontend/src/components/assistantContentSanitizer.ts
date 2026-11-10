export interface SanitizedAssistantContent {
  content: string
  hadControlMarkup: boolean
}

const PIPE_PAIR = String.raw`(?:[|｜]\s*){1,2}`
const DSML_PREFIX = String.raw`${PIPE_PAIR}\s*DSML\s*${PIPE_PAIR}`
const DSML_BLOCK = new RegExp(
  String.raw`<\s*${DSML_PREFIX}\s*tool_calls\s*>[\s\S]*?<\s*\/\s*${DSML_PREFIX}\s*tool_calls\s*>`,
  'gi',
)
const DSML_TAG = new RegExp(String.raw`<\s*\/?\s*${DSML_PREFIX}[^>]*>`, 'gi')
const CONVERGENCE_LINE = /(检测到重复工具调用|本次调查已达到本轮调查预算)[^\n]*/

export function sanitizeAssistantContent(content: string): SanitizedAssistantContent {
  const hadControlMarkup = new RegExp(DSML_BLOCK.source, 'i').test(content)
    || new RegExp(DSML_TAG.source, 'i').test(content)
  if (!hadControlMarkup) return { content, hadControlMarkup: false }

  const cleaned = content
    .replace(DSML_BLOCK, '')
    .replace(DSML_TAG, '')
    .replace(/\n[ \t]*\n(?:[ \t]*\n)+/g, '\n\n')
    .trim()
  const convergence = cleaned.match(CONVERGENCE_LINE)?.[0]?.trim()
  if (convergence) {
    return {
      hadControlMarkup: true,
      content: `${convergence}\n\n模型尝试继续调用工具，但内部工具控制内容已隐藏。请以下方已经获取的数据证据为准。`,
    }
  }
  return {
    hadControlMarkup: true,
    content: cleaned || '内部工具控制内容已隐藏。请查看本消息下方已经获取的数据证据。',
  }
}
