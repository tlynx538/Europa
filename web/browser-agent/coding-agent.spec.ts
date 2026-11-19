import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

// Phase 4D Stage 2 supervised Coding Agent against the deterministic demo provider (not an LLM)
// and the real local Gateway this config starts. Every wait is on persisted state: roadmap and
// execution records from the API, stream messages, approval cards and cell outputs.
const captures = resolve('../docs/screenshots/phase4d')
const desktop = { width: 1360, height: 900 }, mobile = { width: 390, height: 844 }
const operation = () => crypto.randomUUID().replaceAll('-', '')
const MATRIX = 'Define a transition matrix'

const coordinator = (page: Page) => page.locator('article.message[data-author="coordinator"]')
const approval = (page: Page, title: string) => page.getByRole('region', { name: `Approval: ${title}` })
// Match a task by its own title button: another task's disabled-reason text may name it.
const task = (page: Page, title: string) => page.locator('#roadmap li.task').filter({ has: page.locator(':scope > button', { hasText: new RegExp(`^(✓ )?${title}$`) }) })
const assignButton = (page: Page, title: string) => task(page, title).getByRole('button', { name: 'Assign to Coding Agent' })

async function capture(page: Page, name: string, focus: Locator, size = desktop, file = `${name}-${size === mobile ? 'mobile' : 'desktop'}.png`, hover = false) {
  mkdirSync(captures, { recursive: true })
  const previous = page.viewportSize()
  await page.setViewportSize(size)
  await page.waitForFunction(width => document.documentElement.clientWidth === width, size.width)
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  await focus.evaluate(element => element.scrollIntoView({ block: 'center' }))
  if (hover) await focus.hover()
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await page.screenshot({ path: join(captures, file), animations: 'disabled' })
  if (previous) await page.setViewportSize(previous)
}

async function decide(page: Page, card: Locator) {
  const decided = page.waitForResponse(r => /\/api\/agent\/approvals\/[a-f0-9]{32}$/.test(r.url()) && r.request().method() === 'POST')
  await card.getByRole('button', { name: 'Approve', exact: true }).click()
  expect((await decided).status()).toBe(200)
}

async function projectNotebook(request: APIRequestContext, folder: string) {
  expect((await request.post('/api/workspace/projects', { data: { path: folder, operation_id: operation() } })).status()).toBe(201)
  const created = await request.post('/api/workspace/notebooks', { data: { path: `${folder}/chain.ipynb`, operation_id: operation() } })
  expect(created.status()).toBe(201)
  const { notebook_id: notebook, project_id: project } = await created.json()
  const cell = await request.post(`/api/notebooks/${notebook}/cells`, { data: { revision: 1, cell_type: 'code', source: 'owner_note = 1' } })
  expect(cell.ok()).toBe(true)
  return { notebook: notebook as string, project: project as string }
}

async function roadmapTasks(request: APIRequestContext, project: string) {
  const items = (await (await request.get(`/api/roadmaps?project_id=${project}`)).json()).items as { data: { tasks: { id: string; title: string; status: string; evidence: unknown[] }[] } }[]
  return items[0]?.data.tasks ?? []
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('europa.panel.left', 'true'))
})

test('coding agent: assign task 1, approve the insert, approve the run, applied receipt and report', async ({ page, request }) => {
  test.setTimeout(240000)
  const { notebook, project } = await projectNotebook(request, 'Coding Study 4D')
  await page.goto(`/notebooks/${notebook}`)
  const composer = page.getByRole('textbox', { name: 'Message' })
  await expect(composer).toBeEditable()

  // Plan approved (Phase 4C path).
  await composer.fill('Plan how to understand this Markov chain.')
  await composer.press('Enter')
  const plan = approval(page, 'Create roadmap')
  await decide(page, plan)
  await expect(coordinator(page).filter({ hasText: 'Saved the roadmap with 4 tasks.' })).toHaveCount(1)
  await expect(page.locator('#roadmap li.task')).toHaveCount(4)

  // Only the ready first task can be assigned; the others explain why.
  await expect(assignButton(page, MATRIX)).toBeEnabled()
  await expect(assignButton(page, 'Simulate transitions')).toBeDisabled()
  await expect(task(page, 'Simulate transitions').locator('.agent-assign-wrap')).toHaveAttribute('title', `Waiting for: ${MATRIX}.`)
  const assigned = page.waitForResponse(r => r.url().endsWith('/api/agent/tasks/assign') && r.request().method() === 'POST')
  await assignButton(page, MATRIX).click()
  const response = await assigned
  expect(response.status()).toBe(201)
  expect(Object.keys(response.request().postDataJSON()).sort()).toEqual(['operation_id', 'roadmap_id', 'task_id'])
  expect((await roadmapTasks(request, project))[0].status).toBe('in_progress')

  // The system/tool assignment message renders, followed by the run panel and its insert approval.
  const message = page.locator('article.message[data-author="system/tool"]', { hasText: `Owner assigned "${MATRIX}" to the Coding Agent.` })
  await expect(message).toHaveCount(1)
  const insert = approval(page, 'Insert code cell')
  await expect(insert).toBeVisible()
  expect(await message.evaluate(element => element.nextElementSibling?.classList.contains('agent-stream') ?? false)).toBe(true)
  expect(await message.evaluate(element => !!element.nextElementSibling?.querySelector('[aria-label="Approval: Insert code cell"]'))).toBe(true)
  await expect(insert.locator('pre.agent-code')).toContainText("print(f'{state}: row sum = {sum(row)}')")
  const approve = insert.getByRole('button', { name: 'Approve', exact: true })
  await expect(approve).toBeEnabled()
  await expect(insert.getByRole('button', { name: 'Deny', exact: true })).toBeEnabled()
  await expect(assignButton(page, 'Estimate the stationary distribution')).toBeDisabled()
  expect((await (await request.get(`/api/notebooks/${notebook}`)).json()).cells).toHaveLength(1)  // nothing inserted yet
  await capture(page, 'assign', message)
  await capture(page, 'assign', message, mobile)
  await capture(page, 'insert-approval', insert)
  await capture(page, 'insert-approval', insert, mobile)
  await capture(page, 'approve', approve, desktop, 'approve-enabled.png')
  await capture(page, 'approve', approve, desktop, 'approve-hover.png', true)
  await page.mouse.move(0, 0)

  // An unsaved cell draft blocks Approve (autosave off so the draft persists); Deny stays enabled.
  await page.getByRole('menubar').getByRole('menuitem', { name: 'View', exact: true }).click()
  await page.getByRole('menuitemcheckbox', { name: /Autosave/ }).click()
  await expect(page.getByRole('menuitemcheckbox', { name: /Autosave/ })).toHaveCount(0)  // the menu closed
  const editor = page.locator('.cell .cm-content').first()
  await editor.click()
  await page.keyboard.press('End')
  await page.keyboard.type('0')
  await expect(page.locator('.save-indicator')).toContainText('Unsaved draft')
  await expect(approve).toBeDisabled()
  await expect(insert.getByText('Save or discard your edits first.')).toBeVisible()
  await expect(approve).toHaveAttribute('aria-describedby', /approval-reason-/)
  await expect(insert.getByRole('button', { name: 'Deny', exact: true })).toBeEnabled()
  await capture(page, 'approve', approve, desktop, 'approve-disabled-drafts.png')
  await editor.click()
  await page.keyboard.press('End')
  await page.keyboard.press('Backspace')  // back to the saved source: the draft is discarded
  await expect(page.locator('.save-indicator')).toContainText('Saved')
  await expect(approve).toBeEnabled()

  // Phase 4D Stage 3 (owner addition): the owner saves an edit while the insert card is pending.
  // The card is stale: Approve stays disabled, Re-propose sends it back, the agent proposes again.
  const runId = (await response.json()).id as string
  const before = ((await (await request.get(`/api/agent/runs/${runId}`)).json()).approvals as { revision: number }[])[0].revision
  await editor.click()
  await page.keyboard.press('End')
  await page.keyboard.type('0')
  await page.getByRole('button', { name: 'Save notebook' }).click()
  await expect(page.locator('.save-indicator')).toContainText('Saved')
  await expect(insert.getByText('Notebook changed since this was proposed.')).toBeVisible()
  await expect(approve).toBeDisabled()
  await expect(insert.getByRole('button', { name: 'Deny', exact: true })).toBeEnabled()
  const repropose = insert.getByRole('button', { name: 'Re-propose', exact: true })
  await expect(repropose).toBeEnabled()
  await capture(page, 'repropose', insert, desktop, 'repropose-stale-desktop.png')
  const sentBack = page.waitForResponse(r => /\/api\/agent\/approvals\/[a-f0-9]{32}$/.test(r.url()) && r.request().method() === 'POST')
  await repropose.click()
  const decision = await sentBack
  expect(decision.status()).toBe(200)
  expect(decision.request().postDataJSON().decision).toBe('repropose')
  await expect(insert.getByText('Notebook changed since this was proposed.')).toHaveCount(0)
  await expect(approve).toBeEnabled()  // a new card, bound to the saved revision
  const current = (await (await request.get(`/api/notebooks/${notebook}`)).json()).metadata.europa.revision as number
  const approvals = (await (await request.get(`/api/agent/runs/${runId}`)).json()).approvals as { state: string; reason: string | null; revision: number }[]
  expect(approvals.map(a => [a.state, a.reason])).toEqual([['denied', 'stale_revision'], ['pending', null]])
  expect(approvals[1].revision).toBe(current)
  expect(current).toBeGreaterThan(before)

  // Approve the insert: the agent's cell appears, then a separate approval to run it.
  await decide(page, insert)
  const run = approval(page, 'Run code cell')
  await expect(run).toBeVisible()
  await expect(run.getByRole('note')).toContainText('Not a sandbox')
  await expect(page.locator('.cell .cm-content', { hasText: 'row sum = {sum(row)}' })).toHaveCount(1)
  // Both the owner's saved edit and the agent's cell persisted.
  const sources = ((await (await request.get(`/api/notebooks/${notebook}`)).json()).cells as { source: string | string[] }[])
    .map(cell => Array.isArray(cell.source) ? cell.source.join('') : cell.source)
  expect(sources[0]).toBe('owner_note = 10')
  expect(sources[1]).toContain("print(f'{state}: row sum = {sum(row)}')")
  expect((await (await request.get(`/api/notebooks/${notebook}/executions?offset=0&limit=100`)).json()).items).toEqual([])  // not run yet
  await expect(run.getByRole('button', { name: 'Approve', exact: true })).toBeEnabled()  // the view caught up with the new revision
  await decide(page, run)

  // An applied receipt, its output in the notebook, and the Coding Agent report.
  await expect.poll(async () => ((await (await request.get(`/api/notebooks/${notebook}/executions?offset=0&limit=100`)).json()).items as { state: string }[])
    .map(item => item.state), { timeout: 90000 }).toEqual(['applied'])
  const output = page.locator('.output-content', { hasText: 'A: row sum = 1.0' })
  await expect(output).toBeVisible({ timeout: 15000 })
  const report = page.locator('article.message[data-author="coding_agent"]')
  await expect(report).toHaveCount(1, { timeout: 30000 })
  await expect(report).toContainText('Execution record: receipt')
  await expect(report).toContainText('applied, status ok.')
  await expect(report).toContainText('A: row sum = 1.0')
  const [first] = await roadmapTasks(request, project)
  expect(first.status).toBe('completed')  // Phase 4D Stage 3: verified by the applied receipt
  expect(first.evidence).toHaveLength(1)
  await capture(page, 'coding-report', report)

  // Phase 4D Stage 3: the Roadmap panel updates without a reload.
  await expect(task(page, MATRIX).locator(':scope > button:not(.task-reopen)')).toHaveText(`✓ ${MATRIX}`)
  await expect(task(page, MATRIX).locator(':scope > button:not(.task-reopen)')).toHaveAttribute('title', /^Completed by Coding Agent — verified by receipt [a-f0-9]{8}$/)
  await expect(page.locator('#roadmap .roadmap-title strong')).toHaveText('25%')
  await expect(page.locator('#roadmap progress[aria-label="Overall project progress"]')).toHaveAttribute('value', '25')
  await expect(assignButton(page, 'Simulate transitions')).toBeEnabled()
  const [, second] = await roadmapTasks(request, project)
  expect(second.status).toBe('pending')
  await capture(page, 'progress-completed', page.locator('#roadmap .roadmap-title'))
  // Mobile: the Roadmap panel is a modal opened from View → Roadmap panel.
  await page.setViewportSize(mobile)
  await page.waitForFunction(width => document.documentElement.clientWidth === width, mobile.width)
  await page.getByRole('menubar').getByRole('menuitem', { name: 'View', exact: true }).click()
  await page.getByRole('menuitemcheckbox', { name: /Roadmap panel/ }).click()
  await expect(page.locator('#roadmap .roadmap-title strong')).toHaveText('25%')
  await expect(task(page, MATRIX).locator(':scope > button:not(.task-reopen)')).toHaveText(`✓ ${MATRIX}`)
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await page.screenshot({ path: join(captures, 'progress-completed-mobile.png'), animations: 'disabled' })
  await page.keyboard.press('Escape')
  await page.setViewportSize(desktop)
})
