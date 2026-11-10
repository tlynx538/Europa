import { expect, type Page } from './browserFixtures'

// Await both the bounded page response and its actual rendered rows. A button
// becoming enabled alone can precede the effect that starts the next request.
export async function findSessionRow(page: Page, title: string) {
  type SessionPage = { items: { title: string }[]; next_cursor: string | null }
  const responseFor = (cursor: string | null) => page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.pathname === '/api/dashboard/sessions' && url.searchParams.get('cursor') === cursor
  })
  const rendered = async (response: Awaited<ReturnType<typeof responseFor>>) => {
    expect(response.ok()).toBe(true)
    const data: SessionPage = await response.json()
    await expect.poll(async () => (await page.locator('.dashboard-sessions .dashboard-filename').allTextContents()).map(value => value.trim())).toEqual(data.items.map(item => item.title))
    await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled()
    return data
  }
  const initial = responseFor(null)
  if (await page.getByRole('tab', { name: 'Running', exact: true }).getAttribute('aria-selected') === 'true') await page.reload()
  else await page.getByRole('tab', { name: 'Running', exact: true }).click()
  let data = await rendered(await initial)
  const row = page.getByRole('row').filter({ has: page.getByRole('button', { name: title, exact: true }) })
  while (!await row.count() && data.next_cursor) {
    const next = responseFor(data.next_cursor)
    await page.getByRole('button', { name: 'Next sessions' }).click()
    data = await rendered(await next)
  }
  await expect(row).toBeVisible()
  return row
}
