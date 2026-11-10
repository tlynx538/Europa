// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useDashboardData } from './useDashboardData'

const now = 1794326400
const session = { id: 'same', title: 'Notebook', available: true, location: 'Home / Work', state: 'Idle', reason: 'Verified Gateway', last_activity: now, observed_at: now, expires_at: now + 15 }
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(now * 1000); vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible') })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const flush = async () => { await act(async () => { await Promise.resolve() }) }

it('loads persisted recent order without observing runtimes on Files and refreshes on explicit visit', async () => {
  let items = [{ id: 'physical', title: 'Physical', location: 'Home / Work', available: true, opened_at: now }, { id: 'private', title: 'Catalog', location: 'Private catalog', available: true, opened_at: now - 1 }]
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ items })))
  vi.stubGlobal('fetch', fetcher)
  const { result } = renderHook(() => useDashboardData(false))
  await flush()
  expect(result.current.recent.map(item => item.id)).toEqual(['physical', 'private'])
  expect(fetcher.mock.calls).toHaveLength(1)
  items = [...items].reverse()
  await act(async () => { dispatchEvent(new Event('europa:recent-visit-recorded')) })
  expect(result.current.recent.map(item => item.id)).toEqual(['private', 'physical'])
})

it('expires old verified labels, isolates failed refresh, and bounds automatic calls', async () => {
  let fail = false
  const fetcher = vi.fn(async (url: string) => {
    if (url.includes('/recent')) return new Response('{"items":[]}')
    if (fail) throw new Error('unavailable')
    return new Response(JSON.stringify({ items: [session], next_cursor: null }))
  })
  vi.stubGlobal('fetch', fetcher)
  const { result } = renderHook(() => useDashboardData(true))
  await flush()
  expect(result.current.sessions[0].state).toBe('Idle')
  fail = true
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(result.current.sessions[0].state).toBe('Unknown')
  expect(result.current.sessions[0].observed_at).toBeNull()
  expect(result.current.sessions[0].title).toBe('Notebook')
  await act(async () => { await vi.advanceTimersByTimeAsync(120000) })
  const count = fetcher.mock.calls.filter(([url]) => url.includes('/sessions')).length
  expect(count).toBe(7)
  await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
  expect(fetcher.mock.calls.filter(([url]) => url.includes('/sessions'))).toHaveLength(count)
  expect(result.current.sessionNotice).toContain('paused')
})

it('does not show an expired response as newly verified and stops requests when Files is selected', async () => {
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(url.includes('/recent') ? { items: [] } : { items: [{ ...session, expires_at: now - 1 }], next_cursor: 'next' })))
  vi.stubGlobal('fetch', fetcher)
  const { result, rerender } = renderHook(({ running }) => useDashboardData(running), { initialProps: { running: true } })
  await flush()
  expect(result.current.sessions[0].state).toBe('Unknown')
  expect(result.current.sessionNotice).toContain('expired')
  rerender({ running: false })
  const calls = fetcher.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(120000) })
  expect(fetcher.mock.calls).toHaveLength(calls)
})

it('keeps notebook rows as Unknown when an explicit Refresh fails', async () => {
  let fail = false
  vi.stubGlobal('fetch', async (url: string) => {
    if (url.includes('/recent')) return new Response('{"items":[]}')
    if (fail) throw new Error('failed observation')
    return new Response(JSON.stringify({ items: [session], next_cursor: null }))
  })
  const { result } = renderHook(() => useDashboardData(true))
  await flush()
  expect(result.current.sessions[0].state).toBe('Idle')
  fail = true
  await act(async () => { result.current.refreshSessions() })
  expect(result.current.sessions).toHaveLength(1)
  expect(result.current.sessions[0].state).toBe('Unknown')
  expect(result.current.sessions[0].observed_at).toBeNull()
})
