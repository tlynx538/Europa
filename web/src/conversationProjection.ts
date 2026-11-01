import type { Comment, Conversation, Message, RecordData } from './applicationApi'

// Placement is captured at message commit, not inferred from cell creation/execution time.
// The server adopts old active canonical messages once; only unexpected unplaced
// records use the conservative context/tail fallback instead of inventing order.
export function projectConversation(notebookId: string, projectId: string, cellIds: string[], conversation: RecordData<Conversation> | undefined, messages: RecordData<Message>[], comments: RecordData<Comment>[]) {
  const cells: Record<string, { messages: RecordData<Message>[]; comments: RecordData<Comment>[] }> = Object.fromEntries(cellIds.map(id => [id, { messages: [], comments: [] }]))
  const beforeCells: RecordData<Message>[] = [], general: RecordData<Message>[] = [], projectMessages: RecordData<Message>[] = [], orphanMessages: RecordData<Message>[] = [], orphanPlacement: RecordData<Message>[] = [], orphanComments: RecordData<Comment>[] = []
  if (conversation?.notebook_id === notebookId) for (const m of messages) {
    if ((m.notebook_id && m.notebook_id !== notebookId) || (m.data.notebook_id && m.data.notebook_id !== notebookId)) continue
    if (Object.prototype.hasOwnProperty.call(m.data, 'placement_after_cell_id')) {
      const anchor = m.data.placement_after_cell_id
      if (anchor === null) beforeCells.push(m)
      else (anchor === undefined ? orphanPlacement : cells[anchor]?.messages ?? orphanPlacement).push(m)
    } else if (m.data.cell_id) (cells[m.data.cell_id]?.messages ?? orphanMessages).push(m)
    else general.push(m)
  }
  for (const c of comments) {
    if (c.data.project_id !== projectId || (c.notebook_id && c.notebook_id !== notebookId) || c.data.parent_id) continue
    (cells[c.data.cell_id]?.comments ?? orphanComments).push(c)
  }
  return { cells, beforeCells, general, projectMessages, orphanMessages, orphanPlacement, orphanComments }
}
