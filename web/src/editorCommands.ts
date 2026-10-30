import { source, type Cell, type Notebook } from './useNotebook'

// Insert uses the store's generated ID; never carry execution metadata or outputs into a copy.
export function copyCell(cell: Cell, drafts: Record<string, string>) {
  return { cell_type: cell.cell_type, source: drafts[cell.id] ?? source(cell) }
}

export function nextRunnableCell(doc: Notebook, from: number) {
  return doc.cells.slice(from).find(cell => cell.cell_type === 'code')?.id
}
