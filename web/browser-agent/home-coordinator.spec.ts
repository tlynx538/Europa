import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

// Phase 4B Home Coordinator journeys against the deterministic demo provider (not an LLM).
// Every wait is on persisted state: thread messages, approval cards and API listings.
const captures = resolve('../docs/screenshots/phase4b')
const desktop = { width: 1360, height: 900 }, mobile = { width: 390, height: 844 }
const panel = (page: Page) => page.locator('details.home-coordinator')
const coordinator = (page: Page) => page.locator('.home-thread [data-author="coordinator"]')
const owner = (page: Page) => page.locator('.home-thread [data-author="user"]')
// Direct-child selectors: the panel's own <summary> must not match nested run-panel summaries.
const panelSummary = (page: Page) => panel(page).locator(':scope > summary')
const runSummaries = (page: Page) => panel(page).locator('.home-thread details.agent-run > summary')
const fileRow = (page: Page, name: string) => page.locator('.dashboard-table .dashboard-filename', { hasText: name })

async function capture(page: Page, name: string, size = desktop) {
  mkdirSync(captures, { recursive: true })
  const previous = page.viewportSize()
  await page.setViewportSize(size)
  // Capture only after layout has settled at the new width; a full-page capture taken mid-resize
  // stitches stale tiles.
  await page.waitForFunction(width => document.documentElement.clientWidth === width, size.width)
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  await page.screenshot({ path: join(captures, `${name}-${size === mobile ? 'mobile' : 'desktop'}.png`), fullPage: true, animations: 'disabled' })
  if (previous) await page.setViewportSize(previous)
}

async function rootNames(request: APIRequestContext) {
  const response = await request.get('/api/workspace?path=')
  expect(response.ok()).toBe(true)
  return ((await response.json()).items as { name: string }[]).map(item => item.name).sort()
}

/** Open Home with a fresh Home conversation so journeys do not share a thread. */
async function freshHome(page: Page) {
  await page.goto('/')
  await expect(panel(page)).toBeVisible()
  await expect(panelSummary(page)).toContainText('Deterministic demo provider (not an LLM)')
  const created = page.waitForResponse(r => r.url().endsWith('/api/agent/home/conversations') && r.request().method() === 'POST')
  await panel(page).getByRole('button', { name: 'New', exact: true }).click()
  expect((await created).status()).toBe(201)
  await expect(panel(page).locator('.home-empty')).toBeVisible()
  await expect(panel(page).getByRole('textbox', { name: 'Message the Coordinator' })).toBeEnabled()
}

async function ask(page: Page, text: string) {
  const box = panel(page).getByRole('textbox', { name: 'Message the Coordinator' })
  await box.fill(text)
  const started = page.waitForResponse(r => r.url().endsWith('/api/agent/runs') && r.request().method() === 'POST')
  await box.press('Enter')
  expect((await started).status()).toBe(201)
  await expect(owner(page).last()).toContainText(text)
}

test('discussion only: reply with a question and nothing is created', async ({ page, request }) => {
  const before = await rootNames(request)
  await freshHome(page)
  await expect(page.getByRole('tab', { name: 'Files', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Running', exact: true })).toBeVisible()
  await ask(page, 'How do inflation and housing prices relate?')
  await expect(coordinator(page).last()).toContainText('Would you like me to set up a notebook for this?')
  await expect(runSummaries(page)).toHaveText(['Completed · 0 actions'])  // fresh thread: exactly one run
  await expect(page.getByRole('region', { name: /^Approval:/ })).toHaveCount(0)
  expect(await rootNames(request)).toEqual(before)
  await capture(page, 'discussion')
  await capture(page, 'discussion', mobile)
})

test('create: Markov chain proposal, approval, Files refresh, open notebook, return and reload', async ({ page, request }) => {
  expect(await rootNames(request)).not.toContain('Markov Chain')
  await freshHome(page)
  await ask(page, 'I want to understand a simple Markov chain.')
  const card = page.getByRole('region', { name: 'Approval: Create resources' })
  await expect(card).toBeVisible()
  await expect(card.getByRole('listitem')).toHaveText(['Create project folder Markov Chain/', 'Create notebook Markov Chain/markov-chain.ipynb'])
  expect(await rootNames(request)).not.toContain('Markov Chain')  // nothing exists before approval
  await expect(fileRow(page, 'Markov Chain')).toHaveCount(0)
  await capture(page, 'approval')
  await capture(page, 'approval', mobile)

  const decided = page.waitForResponse(r => /\/api\/agent\/approvals\/[a-f0-9]{32}$/.test(r.url()) && r.request().method() === 'POST')
  await card.getByRole('button', { name: 'Approve', exact: true }).click()
  const decision = await decided
  expect(decision.status()).toBe(200)
  expect(Object.keys(decision.request().postDataJSON()).sort()).toEqual(['decision', 'digest', 'operation_id'])
  const reply = coordinator(page).filter({ hasText: 'Created Markov Chain/markov-chain.ipynb' })
  await expect(reply).toHaveCount(1)
  await expect(fileRow(page, 'Markov Chain')).toBeVisible()  // no manual refresh
  await expect(fileRow(page, 'Markov Chain')).toContainText('Project')
  expect(await rootNames(request)).toContain('Markov Chain')
  const inside = await (await request.get('/api/workspace?path=Markov%20Chain')).json()
  expect((inside.items as { name: string }[]).map(item => item.name)).toEqual(['markov-chain.ipynb'])
  await expect(runSummaries(page)).toHaveText(['Completed · 1 action'])
  await capture(page, 'created')
  await capture(page, 'created', mobile)

  await reply.getByRole('button', { name: 'Open notebook' }).click()
  await expect(page).toHaveURL(/\/notebooks\/[a-f0-9]{32}$/)
  const notebookId = new URL(page.url()).pathname.split('/').at(-1)!
  const origin = page.locator('article.message', { hasText: 'Notebook created from a Home conversation' })
  await expect(origin).toBeVisible()
  await expect(origin).toContainText('Purpose: Owner objective: I want to understand a simple Markov chain.')
  await capture(page, 'notebook-origin')

  await page.getByRole('link', { name: 'Europa', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(coordinator(page).filter({ hasText: 'Created Markov Chain/markov-chain.ipynb' })).toHaveCount(1)
  await expect(owner(page).last()).toContainText('I want to understand a simple Markov chain.')

  await page.reload()
  await expect(coordinator(page).filter({ hasText: 'Created Markov Chain/markov-chain.ipynb' })).toHaveCount(1)
  await expect(fileRow(page, 'Markov Chain')).toBeVisible()
  await expect(page.getByRole('region', { name: /^Approval:/ })).toHaveCount(0)
  const open = coordinator(page).filter({ hasText: 'Created Markov Chain/markov-chain.ipynb' }).getByRole('button', { name: 'Open notebook' })
  await open.click()
  await expect(page).toHaveURL(new RegExp(`/notebooks/${notebookId}$`))
})

test('denial survives reload and creates nothing', async ({ page, request }) => {
  expect(await rootNames(request)).not.toContain('Titanic Survival')
  await freshHome(page)
  await ask(page, 'Set up a notebook to explore Titanic survival')
  await expect(page.getByRole('region', { name: 'Approval: Create resources' })).toBeVisible()
  await page.reload()
  const card = page.getByRole('region', { name: 'Approval: Create resources' })
  await expect(card).toBeVisible()  // the pending approval is persisted, not page state
  await expect(card.getByRole('listitem')).toHaveText(['Create project folder Titanic Survival/', 'Create notebook Titanic Survival/titanic-survival.ipynb'])
  await expect(panel(page).getByRole('button', { name: 'Stop agent' })).toBeVisible()
  const decided = page.waitForResponse(r => /\/api\/agent\/approvals\/[a-f0-9]{32}$/.test(r.url()) && r.request().method() === 'POST')
  await card.getByRole('button', { name: 'Deny', exact: true }).click()
  expect((await decided).status()).toBe(200)
  await expect(coordinator(page).last()).toContainText('Nothing was created')
  await expect(page.getByRole('region', { name: /^Approval:/ })).toHaveCount(0)
  expect(await rootNames(request)).not.toContain('Titanic Survival')
  await expect(fileRow(page, 'Titanic Survival')).toHaveCount(0)
  await capture(page, 'denied')
  await page.reload()
  await expect(coordinator(page).last()).toContainText('Nothing was created')
})
