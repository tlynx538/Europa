// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { InlineCommentComposer, clearSentCommentDraft, commentDraftKey } from './InlineCommentComposer'

afterEach(() => { cleanup(); localStorage.clear() })
const base = { notebook: 'n1', project: 'p1', cell: 'c1', parent: null, source: 'print(1)', revision: 2, invalid: false, busy: false, pending: false, onCancel: vi.fn(), onSend: vi.fn(async () => true) }

it('keeps cancelled multiline comments scoped to notebook, project, cell and parent across remounts', () => {
  const first = render(<InlineCommentComposer {...base} />)
  const input = screen.getByRole('textbox', { name: /Comment/ })
  expect(document.activeElement).toBe(input)
  fireEvent.change(input, { target: { value: 'first\nsecond' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  expect(base.onSend).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(base.onCancel).toHaveBeenCalled()
  first.unmount()
  const reopened = render(<InlineCommentComposer {...base} />)
  expect((screen.getByRole('textbox', { name: /Comment/ }) as HTMLTextAreaElement).value).toBe('first\nsecond')
  reopened.unmount()
  for (const changed of [{ notebook: 'n2' }, { project: 'p2' }, { cell: 'c2' }, { parent: 'root' }]) {
    const view = render(<InlineCommentComposer {...base} {...changed} />)
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('')
    view.unmount()
  }
  expect(JSON.parse(localStorage.getItem(commentDraftKey('n1', 'p1', 'c1', null))!)).toMatchObject({ content: 'first\nsecond', source: 'print(1)' })
})

it('preserves failed and unknown sends, clears only matching acknowledgements and not newer content', async () => {
  const key = commentDraftKey('n1', 'p1', 'c1', 'root')
  const failed = vi.fn(async (_content: string) => false)
  const view = render(<InlineCommentComposer {...base} parent="root" onSend={failed} />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'original' } })
  fireEvent.click(screen.getByRole('button', { name: 'Post reply' }))
  expect(failed.mock.calls[0][0]).toBe('original')
  expect(JSON.parse(localStorage.getItem(key)! ).content).toBe('original')
  clearSentCommentDraft('n1', 'p1', 'c1', 'root', 'different')
  expect(localStorage.getItem(key)).not.toBeNull()
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'newer' } })
  clearSentCommentDraft('n1', 'p1', 'c1', 'root', 'original')
  expect(JSON.parse(localStorage.getItem(key)!).content).toBe('newer')
  view.unmount()
  clearSentCommentDraft('n1', 'p1', 'c1', 'root', 'newer')
  expect(localStorage.getItem(key)).toBeNull()
})

it('blocks missing, changed, unsaved or conflicted cell context without retargeting the reply', () => {
  localStorage.setItem(commentDraftKey('n1', 'p1', 'c1', null), JSON.stringify({ content: 'keep', source: 'old', revision: 1 }))
  const view = render(<InlineCommentComposer {...base} />)
  expect((screen.getByRole('button', { name: 'Post comment' }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByRole('alert').textContent).toContain('changed')
  fireEvent.click(screen.getByRole('button', { name: 'Use current saved cell' }))
  expect((screen.getByRole('button', { name: 'Post comment' }) as HTMLButtonElement).disabled).toBe(false)
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('keep')
  view.rerender(<InlineCommentComposer {...base} invalid />)
  expect((screen.getByRole('button', { name: 'Post comment' }) as HTMLButtonElement).disabled).toBe(true)
  view.unmount()
  localStorage.setItem(commentDraftKey('n1', 'p1', 'c1', 'root'), JSON.stringify({ content: 'historical reply', source: 'old', revision: 1 }))
  const reply = render(<InlineCommentComposer {...base} parent="root" source="" revision={undefined} />)
  expect((screen.getByRole('button', { name: 'Post reply' }) as HTMLButtonElement).disabled).toBe(false)
  reply.rerender(<InlineCommentComposer {...base} parent="root" invalid />)
  expect((screen.getByRole('button', { name: 'Post reply' }) as HTMLButtonElement).disabled).toBe(true)
  expect(base.onSend).not.toHaveBeenCalled()
})

it('sends the exact whitespace-bearing body for matching retry and leaves newer typed text after acknowledgement', async () => {
  let acknowledge!: (value: boolean) => void
  const onSend = vi.fn((_content: string) => new Promise<boolean>(resolve => { acknowledge = resolve }))
  render(<InlineCommentComposer {...base} onSend={onSend} />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '  original  ' } })
  fireEvent.click(screen.getByRole('button', { name: 'Post comment' }))
  expect(onSend.mock.calls[0][0]).toBe('  original  ')
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'newer content' } })
  acknowledge(true)
  await Promise.resolve()
  const key = commentDraftKey('n1', 'p1', 'c1', null)
  expect(JSON.parse(localStorage.getItem(key)!).content).toBe('newer content')
  clearSentCommentDraft('n1', 'p1', 'c1', null, '  original  ')
  expect(JSON.parse(localStorage.getItem(key)!).content).toBe('newer content')
})
