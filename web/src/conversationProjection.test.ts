import { expect, it } from 'vitest'
import { projectConversation } from './conversationProjection'
import type { RecordData, Message, Comment, Conversation } from './applicationApi'

const message = (id: string, links: Partial<Message> = {}): RecordData<Message> => ({ id, notebook_id: null, revision: 1, created_at: 1, data: { content: id, author_role: 'user', parent_id: null, state: 'published', ...links } })
const comment = (id: string, cell_id: string): RecordData<Comment> => ({ id, notebook_id: 'current', revision: 1, created_at: 1, data: { project_id: 'project', cell_id, content: id, parent_id: null, resolved: false, thread_id: id, anchor_status: 'current', classification: 'question', author_role: 'user' } })
const conversation = (notebook_id: string | null): RecordData<Conversation> => ({ id: 'conversation', notebook_id, created_at: 1, revision: 1, data: { title: 'Discussion' } })

it('groups current notebook links in cell order, keeps missing references and filters other notebook records', () => {
  const result = projectConversation('current', 'project', ['a', 'b'], conversation('current'), [message('b1', { cell_id: 'b' }), message('elsewhere', { notebook_id: 'other' }), message('b2', { cell_id: 'b' }), message('missing', { cell_id: 'gone' }), message('general')], [comment('note', 'a'), comment('orphan', 'gone')])
  expect(result.cells.a.comments.map(c => c.id)).toEqual(['note'])
  expect(result.cells.b.messages.map(m => m.id)).toEqual(['b1', 'b2'])
  expect(result.general.map(m => m.id)).toEqual(['general'])
  expect(result.orphanMessages.map(m => m.id)).toEqual(['missing'])
  expect(result.orphanComments.map(c => c.id)).toEqual(['orphan'])
  expect(JSON.stringify(result)).not.toContain('elsewhere')
})

it('does not appropriate project-wide legacy messages or another notebook thread', () => {
  expect(projectConversation('current', 'project', ['a'], conversation(null), [message('legacy')], []).general).toEqual([])
  expect(projectConversation('current', 'project', ['a'], conversation('other'), [message('unsafe')], []).general).toEqual([])
})

it('interleaves committed messages with saved cell traversal without treating context as placement', () => {
  const records = [message('before', { placement_after_cell_id: null }), message('after-a', { placement_after_cell_id: 'a' }),
    message('after-b', { placement_after_cell_id: 'b', cell_id: 'a' }), message('later', { placement_after_cell_id: 'b' })]
  const result = projectConversation('current', 'project', ['a', 'b'], conversation('current'), records, [])
  expect(result.beforeCells.map(m => m.id)).toEqual(['before'])
  expect(result.cells.a.messages.map(m => m.id)).toEqual(['after-a'])
  expect(result.cells.b.messages.map(m => m.id)).toEqual(['after-b', 'later'])
  expect(result.cells.b.messages[0].data.cell_id).toBe('a')
  expect(result.general).toEqual([])
  // Reorder traverses actual saved notebook order; it does not rewrite placement or context.
  const reordered = projectConversation('current', 'project', ['b', 'a'], conversation('current'), records, [])
  expect(reordered.cells.b.messages[0].data.placement_after_cell_id).toBe('b')
})

it('keeps deleted anchors and missing context visible without silently retargeting', () => {
  const records = [message('deleted-anchor', { placement_after_cell_id: 'gone', cell_id: 'a' }),
    message('missing-context', { placement_after_cell_id: 'a', cell_id: 'gone' }), message('legacy-general')]
  const result = projectConversation('current', 'project', ['a'], conversation('current'), records, [])
  expect(result.orphanPlacement.map(m => m.id)).toEqual(['deleted-anchor'])
  expect(result.cells.a.messages.map(m => m.id)).toEqual(['missing-context'])
  expect(result.general.map(m => m.id)).toEqual(['legacy-general'])
})
