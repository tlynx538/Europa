import { useCallback, useEffect, useRef, useState } from 'react'
import { request } from './api'
import { recentVisitRecorded } from './Home'
import type { SessionState } from './Dashboard'

export type RecentNotebook = { id: string; title: string; location: string; available: boolean; opened_at: number }
export type ObservedSession = { id: string; title: string; location: string; available: boolean; state: SessionState; reason: string; last_activity: number | null; observed_at: number | null; expires_at: number | null }
type Page = { items: ObservedSession[]; next_cursor: string | null }

// One bounded page at a time. No requests when hidden; at most six automatic
// refreshes per activation/manual refresh, then explicit Refresh. Never replay execution.
export function useDashboardData(running: boolean) {
  const [recent, setRecent] = useState<RecentNotebook[]>([]), [recentError, setRecentError] = useState('')
  const [page, setPage] = useState<Page>({ items: [], next_cursor: null }), [loading, setLoading] = useState(false)
  const [sessionError, setSessionError] = useState(''), [refresh, setRefresh] = useState(0), [history, setHistory] = useState<string[]>([])
  const [now, setNow] = useState(() => Date.now() / 1000), [paused, setPaused] = useState(false)
  const recentController = useRef<AbortController | null>(null)
  const refreshRecent = useCallback(() => {
    recentController.current?.abort()
    const controller = new AbortController(); recentController.current = controller
    void request<{ items: RecentNotebook[] }>('/dashboard/recent', { signal: controller.signal }).then(data => {
      if (!Array.isArray(data.items)) throw new Error('Invalid recent response')
      if (!controller.signal.aborted) { setRecent(data.items); setRecentError('') }
    }).catch(() => { if (!controller.signal.aborted) { setRecent([]); setRecentError('Recent notebooks unavailable') } })
  }, [])
  useEffect(() => {
    refreshRecent()
    addEventListener(recentVisitRecorded, refreshRecent)
    return () => { recentController.current?.abort(); removeEventListener(recentVisitRecorded, refreshRecent) }
  }, [refreshRecent])
  const pageKey = useRef<string | null>(null)
  const cursor = history.at(-1)
  useEffect(() => {
    if (!running) { pageKey.current = null; return }
    let alive = true, count = 0
    let timer: ReturnType<typeof setTimeout>
    const controller = new AbortController()
    if (pageKey.current !== (cursor ?? 'first')) { setPage({ items: [], next_cursor: null }); pageKey.current = cursor ?? 'first' }
    setSessionError(''); setPaused(false)
    const load = async () => {
      if (!alive) return
      if (document.visibilityState === 'hidden') { timer = setTimeout(() => void load(), 10000); return }
      setLoading(true)
      try {
        const next = await request<Page>(`/dashboard/sessions${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`, { signal: controller.signal })
        if (!Array.isArray(next.items)) throw new Error('Invalid session response')
        if (alive) { setPage(next); setSessionError(''); setNow(Date.now() / 1000) }
      } catch {
        if (alive) {
          setPage(previous => ({ ...previous, items: previous.items.map(item => ({ ...item, state: 'Unknown', observed_at: null, expires_at: null, reason: 'Refresh failed; status is not verified' })) }))
          setSessionError('Runtime observation unavailable. Refresh to try again.')
        }
      } finally {
        if (alive) {
          setLoading(false)
          if (++count < 7) timer = setTimeout(() => void load(), 10000)
          else setPaused(true)
        }
      }
    }
    void load()
    const clock = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => { alive = false; controller.abort(); clearTimeout(timer); clearInterval(clock) }
  }, [running, cursor, refresh])
  const ageNow = Math.max(now, Date.now() / 1000)
  const expired = page.items.some(item => item.expires_at !== null && item.expires_at <= ageNow)
  const sessions = page.items.map(item => item.expires_at !== null && item.expires_at <= ageNow ? { ...item, state: 'Unknown' as const, reason: 'Observation expired; refresh to verify', observed_at: null } : item)
  return { recent, recentError, refreshRecent, sessions, loading, sessionError,
    sessionNotice: expired ? 'Observation expired. Refresh to verify current state.' : paused ? 'Automatic refresh paused. Refresh to continue.' : '',
    refreshSessions: () => setRefresh(value => value + 1),
    next: page.next_cursor ? () => setHistory(value => [...value, page.next_cursor!]) : undefined,
    previous: history.length ? () => setHistory(value => value.slice(0, -1)) : undefined }
}
