export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  toolCalls?: ToolCallResult[]
}

export interface ToolCallResult {
  id: string
  toolName: string
  args: Record<string, unknown>
  result?: unknown
  status: 'pending' | 'success' | 'error'
}
