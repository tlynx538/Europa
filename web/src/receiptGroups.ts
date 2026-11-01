export function groupReceipts<T extends { cell_id: string }>(receipts: T[], cellIds: string[]) {
  const cells: Record<string, T[]> = Object.fromEntries(cellIds.map(id => [id, []]))
  const orphans: T[] = []
  for (const receipt of receipts) {
    if (Object.hasOwn(cells, receipt.cell_id)) cells[receipt.cell_id].push(receipt)
    else orphans.push(receipt)
  }
  return { cells, orphans }
}
