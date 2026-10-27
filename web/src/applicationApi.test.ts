// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { change, pendingMutation, retryMutation, rawTask, type TaskView } from './applicationApi'

afterEach(() => { localStorage.clear(); vi.unstubAllGlobals() })
describe('application operation identity', () => {
  it('retains exact request and operation ID after disconnection', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockResolvedValueOnce(new Response(JSON.stringify({ id: 'saved' })))
    vi.stubGlobal('fetch', fetcher)
    await expect(change('/projects', 'POST', { title: 'Research' })).rejects.toThrow('offline')
    expect(pendingMutation()).not.toBeNull()
    await expect(change('/projects', 'POST', { title: 'Different' })).rejects.toThrow('unknown outcome')
    await expect(retryMutation()).resolves.toEqual({ id: 'saved' })
    expect(fetcher.mock.calls[0][1].body).toBe(fetcher.mock.calls[1][1].body)
    expect(pendingMutation()).toBeNull()
  })
  it('does not send server-derived presentation fields back as task input', () => {
    const task = { id: 'a', title: 'a', acceptance_criteria: ['review'], parent_id: null, dependencies: [], status: 'pending', explanation: null, weight: 1, links: {}, evidence: [], accepted: false,
      progress: 0, display: { compact: true, checkmark: false, show_progress: false }, accepted_by: null } satisfies TaskView
    expect(rawTask(task)).not.toHaveProperty('progress')
    expect(rawTask(task)).not.toHaveProperty('display')
    expect(rawTask(task)).not.toHaveProperty('accepted_by')
  })
})
