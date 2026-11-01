// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ConversationComposer, draftKey, clearSentDraft } from './ConversationComposer'

afterEach(() => { cleanup(); localStorage.clear() })
const props = { notebookId: 'book', conversationId: 'one', cells: [{ id: 'cell', source: 'saved' }], drafts: [], conflict: false, revision: 3, selectedCell: 'cell', reference: null, reply: null, onReplyConsumed: () => {}, busy: false, pending: false, onSend: vi.fn(async () => true) }

it('adopts one prototype draft from the same notebook without deleting its source until acknowledgement', () => {
  const view = render(<ConversationComposer {...props} />)
  fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'unsent' } })
  view.unmount()
  render(<ConversationComposer {...props} conversationId="two" />)
  expect((screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement).value).toBe('unsent')
  expect(localStorage.getItem(draftKey('book', 'one'))).toContain('unsent')
  cleanup()
  render(<ConversationComposer {...props} />)
  expect((screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement).value).toBe('unsent')
})

it('does not clear a newer draft when an acknowledged exact retry matches an older send', () => {
  localStorage.setItem(draftKey('book', 'one'), JSON.stringify({ content: 'newer' }))
  clearSentDraft('book', 'one', 'older')
  expect(localStorage.getItem(draftKey('book', 'one'))).toContain('newer')
  clearSentDraft('book', 'one', 'newer')
  expect(localStorage.getItem(draftKey('book', 'one'))).toBeNull()
})

it('does not send a dirty cell reference as a saved link', () => {
  render(<ConversationComposer {...props} drafts={['cell']} reference={{ id: 'cell', source: 'saved', revision: 3 }} />)
  fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'question' } })
  expect(screen.getByRole('button', { name: 'Send' }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByRole('alert').textContent).toContain('unsaved')
})

it('removes a resend-ready draft after an exact pending operation is acknowledged', () => {
  localStorage.setItem(draftKey('book', 'one'), JSON.stringify({ content: 'sent' }))
  const view = render(<ConversationComposer {...props} pending />)
  expect((screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement).value).toBe('sent')
  clearSentDraft('book', 'one', 'sent')
  view.rerender(<ConversationComposer {...props} pending={false} />)
  expect((screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement).value).toBe('')
  expect(screen.getByRole('button', { name: 'Send' }).hasAttribute('disabled')).toBe(true)
})

it('sends only persisted notebook and saved-cell links, without claiming an author role', async () => {
  const onSend = vi.fn(async () => true)
  render(<ConversationComposer {...props} onSend={onSend} reference={{ id: 'cell', source: 'saved', revision: 3 }} />)
  fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'Is this result right?' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(onSend).toHaveBeenCalledWith({ content: 'Is this result right?', parent_id: null, state: 'published', notebook_id: 'book', cell_id: 'cell', document_revision: 3 }, 'Is this result right?'))
})

it('never adopts a draft from another notebook', () => {
  localStorage.setItem(draftKey('other', 'old'), JSON.stringify({ content: 'private' }))
  render(<ConversationComposer {...props} conversationId="canonical" />)
  expect((screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement).value).toBe('')
})
