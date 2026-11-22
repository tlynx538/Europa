import { writeFileSync } from 'node:fs'
import { expect, type APIRequestContext, type APIResponse, type Page, type TestInfo } from './browserFixtures'

type SessionItem = { id: string; title: string; location: string; state: string }
type SessionTarget = { id: string } | { title: string; location: string }

// Poll the authoritative sessions API across every page until the target
// notebook reports a settled state (Idle, Stopped). Under machine load a kernel
// can take longer than the UI expect timeout to settle; the UI assertion that
// follows stays unchanged. On timeout every fetched page is saved as evidence.
export function waitForSessionState(request: APIRequestContext, testInfo: TestInfo, target: SessionTarget, state: string) {
  return pollSessionState(request, testInfo, target, state, 30_000, [250, 500, 1000])
}

// Busy is transient: confirm it only while the test still holds the execution
// open (before releasing it), polling every page at a short interval.
export function confirmHeldSessionState(request: APIRequestContext, testInfo: TestInfo, target: SessionTarget, state: string, timeout = 15_000) {
  return pollSessionState(request, testInfo, target, state, timeout, [100, 250])
}

async function pollSessionState(request: APIRequestContext, testInfo: TestInfo, target: SessionTarget, state: string, timeout: number, intervals: number[]) {
  const matches = (item: SessionItem) => 'id' in target ? item.id === target.id : item.title === target.title && item.location === target.location
  const label = 'id' in target ? `notebook ${target.id}` : `notebook "${target.title}" at "${target.location}"`
  let pages: { cursor: string | null; status: number; body: unknown }[] = []
  let observed = 'Missing'
  const observe = async () => {
    pages = []
    observed = 'Missing'
    let cursor: string | null = null
    do {
      const response: APIResponse = await request.get(`/api/dashboard/sessions${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`)
      if (!response.ok()) {
        pages.push({ cursor, status: response.status(), body: await response.text() })
        observed = `HTTP ${response.status()}`
        return observed
      }
      const body: { items: SessionItem[]; next_cursor: string | null } = await response.json()
      pages.push({ cursor, status: response.status(), body })
      const item = body.items.find(matches)
      if (item) observed = item.state
      cursor = body.next_cursor ?? null
    } while (cursor)
    return observed
  }
  try {
    await expect.poll(observe, { timeout, intervals }).toBe(state)
  } catch {
    const key = ('id' in target ? target.id : target.title).replace(/[^A-Za-z0-9_-]+/g, '-')
    const path = testInfo.outputPath(`dashboard-sessions-${key}-${state}.json`)
    writeFileSync(path, JSON.stringify({ target, expected: state, observed, at: new Date().toISOString(), timeoutMs: timeout, pages }, null, 2))
    await testInfo.attach(`dashboard-sessions-${key}-${state}`, { path, contentType: 'application/json' })
    throw new Error(`Dashboard sessions API did not report ${label} as ${state} within ${timeout} ms; last observed ${observed}`)
  }
}

// Await both the bounded page response and its actual rendered rows. A button
// becoming enabled alone can precede the effect that starts the next request.
// Only responses to requests started after the triggering click count, so an
// automatic poll already in flight cannot satisfy the wait with older state.
// An already open Running tab is refreshed with the dashboard's own controls
// (Previous sessions back to the first page, then Refresh), never a page reload.
export async function findSessionRow(page: Page, title: string) {
  type SessionPage = { items: { title: string }[]; next_cursor: string | null }
  const responseFor = (cursor: string | null | undefined) => {
    const since = Date.now()
    return page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === '/api/dashboard/sessions' && (cursor === undefined || url.searchParams.get('cursor') === cursor)
        && response.request().timing().startTime >= since
    })
  }
  const rendered = async (response: Awaited<ReturnType<typeof responseFor>>) => {
    expect(response.ok()).toBe(true)
    const data: SessionPage = await response.json()
    await expect.poll(async () => (await page.locator('.dashboard-sessions .dashboard-filename').allTextContents()).map(value => value.trim())).toEqual(data.items.map(item => item.title))
    await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled()
    return data
  }
  const running = page.getByRole('tab', { name: 'Running', exact: true })
  let data: SessionPage
  if (await running.getAttribute('aria-selected') === 'true') {
    const previous = page.getByRole('button', { name: 'Previous sessions', exact: true })
    while (await previous.count()) {
      const back = responseFor(undefined)
      await previous.click()
      await rendered(await back)
    }
    const refreshed = responseFor(null)
    await page.getByRole('button', { name: 'Refresh', exact: true }).click()
    data = await rendered(await refreshed)
  } else {
    const initial = responseFor(null)
    await running.click()
    data = await rendered(await initial)
  }
  const row = page.getByRole('row').filter({ has: page.getByRole('button', { name: title, exact: true }) })
  while (!await row.count() && data.next_cursor) {
    const next = responseFor(data.next_cursor)
    await page.getByRole('button', { name: 'Next sessions' }).click()
    data = await rendered(await next)
  }
  await expect(row).toBeVisible()
  return row
}
