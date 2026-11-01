import { describe, expect, it } from 'vitest'
import { copyCell, nextRunnableCell } from './editorCommands'
import type { Notebook } from './useNotebook'

const doc: Notebook = { metadata: { europa: { notebook_id: 'n', title: 'N', revision: 1 } }, cells: [
  { id: 'a', cell_type: 'code', source: 'print(1)', metadata: {}, outputs: [] },
  { id: 'b', cell_type: 'markdown', source: 'note', metadata: {} },
  { id: 'c', cell_type: 'code', source: 'print(2)', metadata: {}, outputs: [] },
] }
describe('editor commands', () => {
  it('copies the current draft without execution artifacts and with a fresh identity', () => {
    const copied = copyCell(doc.cells[0], { a: 'print(3)' })
    expect(copied).toEqual({ cell_type: 'code', source: 'print(3)' })
  })
  it('runs code cells in document order, skipping markdown', () => {
    expect(nextRunnableCell(doc, 0)).toBe('a')
    expect(nextRunnableCell(doc, 1)).toBe('c')
    expect(nextRunnableCell(doc, 3)).toBeUndefined()
  })
})
