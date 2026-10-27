import { describe, expect, it, vi, afterEach } from 'vitest'
import { ApiError, pollReceipt, request, terminalReceipt } from './api'

afterEach(() => vi.unstubAllGlobals())
describe('execution receipts', () => {
  it('polls the accepted execution identity until a terminal state', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ execution_id: 'e1', cell_id: 'c', state: 'running' })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ execution_id: 'e1', cell_id: 'c', state: 'applied' })))
    vi.stubGlobal('fetch', fetcher)
    const states: string[] = []
    await pollReceipt('nb', 'e1', new AbortController().signal, r => states.push(r.state), 0)
    expect(states).toEqual(['running', 'applied'])
    expect(fetcher.mock.calls[0][0]).toBe('/api/notebooks/nb/executions/e1')
  })
  it('does not confuse completed with applied or uncertainty with success', () => {
    expect(terminalReceipt('completed')).toBe(false)
    expect(terminalReceipt('uncertain')).toBe(true)
  })
  it('surfaces revision conflicts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'stale' }), { status: 409 })))
    await expect(request('/notebooks/x')).rejects.toMatchObject({ status: 409, message: 'stale' } satisfies Partial<ApiError>)
  })
})
