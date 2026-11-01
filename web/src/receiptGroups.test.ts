import { expect, it } from 'vitest'
import { groupReceipts } from './receiptGroups'

it('keeps all immutable receipts under their cell and retains deleted-cell receipts separately', () => {
  const receipts = [
    { cell_id: 'one', execution_id: 'first', state: 'applied' },
    { cell_id: 'gone', execution_id: 'orphan', state: 'conflict' },
    { cell_id: 'one', execution_id: 'second', state: 'uncertain' },
  ]
  const grouped = groupReceipts(receipts, ['one', 'two'])
  expect(grouped.cells.one.map(r => r.execution_id)).toEqual(['first', 'second'])
  expect(grouped.cells.two).toEqual([])
  expect(grouped.orphans.map(r => r.execution_id)).toEqual(['orphan'])
})
