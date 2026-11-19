import type { Message, RecordData } from './applicationApi'

// One-shot navigation flag: Home's "Open notebook" asks the notebook view to reveal the
// kickoff questions once they arrive. In memory only, so a reload never re-triggers it.
let pending: string | null = null

export const requestKickoffFocus = (notebookId: string) => { pending = notebookId }
export const kickoffFocusPending = (notebookId: string) => pending === notebookId
export const consumeKickoffFocus = () => { pending = null }

/** The newest Coordinator message marked needs_input that no owner message has answered yet. */
export function openQuestion(messages: RecordData<Message>[]): RecordData<Message> | null {
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index]
    if (message.data.author_role === 'user') return null
    if (message.data.author_role === 'coordinator' && message.data.state === 'needs_input') return message
  }
  return null
}

/** True while the owner is editing a notebook cell; focus must not be taken from it. */
export const editingCell = () => !!(document.activeElement instanceof HTMLElement && document.activeElement.closest('.cm-editor'))
