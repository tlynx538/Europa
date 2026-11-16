import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

// Phase 4C Notebook Coordinator journeys against the deterministic demo provider (not an LLM).
// Every wait is on persisted state: stream messages, approval cards, run panels and API records.
// Each journey uses its own project folder; the 4B journeys in this suite create "Markov Chain".
const captures = resolve('../docs/screenshots/phase4c')
const desktop = { width: 1360, height: 900 }, mobile = { width: 390, height: 844 }
const operation = () => crypto.randomUUID().replaceAll('-', '')
const MARKOV = ['Define a transition matrix', 'Simulate transitions', 'Estimate the stationary distribution', 'Visualize convergence']

const owner = (page: Page) => page.locator('article.message[data-author="user"]')
const coordinator = (page: Page) => page.locator('article.message[data-author="coordinator"]')
const approval = (page: Page, title: string) => page.getByRole('region', { name: `Approval: ${title}` })
const runSummaries = (page: Page) => page.locator('.agent-stream details.agent-run > summary')
const roadmapTasks = (page: Page) => page.locator('#roadmap .roadmap li.task')

/** The stream scrolls inside .notebook-editor, so bring `focus` to the end of the visible stream first. */
async function capture(page: Page, name: string, focus: Locator, size = desktop) {
  mkdirSync(captures, { recursive: true })
  const previous = page.viewportSize()
  await page.setViewportSize(size)
  await page.waitForFunction(width => document.documentElement.clientWidth === width, size.width)
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  await focus.evaluate(element => element.scrollIntoView({ block: 'end' }))
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await page.screenshot({ path: join(captures, `${name}-${size === mobile ? 'mobile' : 'desktop'}.png`), animations: 'disabled' })
  if (previous) await page.setViewportSize(previous)
}

/** A project folder with one notebook (and one code cell), created through the workspace API. */
async function projectNotebook(request: APIRequestContext, folder: string) {
  expect((await request.post('/api/workspace/projects', { data: { path: folder, operation_id: operation() } })).status()).toBe(201)
  const created = await request.post('/api/workspace/notebooks', { data: { path: `${folder}/chain.ipynb`, operation_id: operation() } })
  expect(created.status()).toBe(201)
  const { notebook_id: notebook, project_id: project } = await created.json()
  const cell = await request.post(`/api/notebooks/${notebook}/cells`, { data: { revision: 1, cell_type: 'code', source: 'P = [[0.9, 0.1], [0.5, 0.5]]' } })
  expect(cell.ok()).toBe(true)
  return { notebook: notebook as string, project: project as string }
}

async function roadmaps(request: APIRequestContext, project: string) {
  const response = await request.get(`/api/roadmaps?project_id=${project}`)
  expect(response.ok()).toBe(true)
  return (await response.json()).items as { id: string; revision: number; data: { objective: string; tasks: { id: string; title: string; status: string }[] } }[]
}

async function openNotebook(page: Page, notebook: string) {
  await page.goto(`/notebooks/${notebook}`)
  await expect(page.getByRole('checkbox', { name: 'Ask Coordinator' })).toBeChecked()
  await expect(page.getByRole('textbox', { name: 'Message' })).toBeEditable()
}

async function ask(page: Page, text: string) {
  const box = page.getByRole('textbox', { name: 'Message' })
  await box.fill(text)
  const posted = page.waitForResponse(r => /\/api\/conversations\/[a-f0-9]{32}\/messages$/.test(r.url()) && r.request().method() === 'POST')
  const started = page.waitForResponse(r => r.url().endsWith('/api/agent/runs') && r.request().method() === 'POST')
  await box.press('Enter')
  expect((await posted).status()).toBe(201)
  const start = await started
  expect(start.status()).toBe(201)
  expect(Object.keys(start.request().postDataJSON()).sort()).toEqual(['conversation_id', 'message_id', 'operation_id'])
  await expect(owner(page).filter({ hasText: text })).toHaveCount(1)
}

async function approve(page: Page, card: ReturnType<typeof approval>) {
  const decided = page.waitForResponse(r => /\/api\/agent\/approvals\/[a-f0-9]{32}$/.test(r.url()) && r.request().method() === 'POST')
  await card.getByRole('button', { name: 'Approve', exact: true }).click()
  const decision = await decided
  expect(decision.status()).toBe(200)
  expect(Object.keys(decision.request().postDataJSON()).sort()).toEqual(['decision', 'digest', 'operation_id'])
}

test.beforeEach(async ({ page }) => {
  // Desktop opens the Roadmap side panel from its persisted preference.
  await page.addInitScript(() => localStorage.setItem('europa.panel.left', 'true'))
})

test('J1 discuss, J2 plan with approval surviving reload and Home round trip, J3 revise, J5 status', async ({ page, request }) => {
  test.setTimeout(180000)
  const { notebook, project } = await projectNotebook(request, 'Chain Study 4C')
  await openNotebook(page, notebook)
  await expect(page.locator('#roadmap')).toContainText('No roadmap yet')

  // J1: discussion changes nothing.
  await ask(page, 'What does this notebook do so far?')
  await expect(coordinator(page).filter({ hasText: /This notebook has 1 cell and is at revision \d+\./ })).toHaveCount(1)
  await expect(runSummaries(page)).toHaveText(['Completed · 0 actions'])
  expect(await roadmaps(request, project)).toEqual([])

  // J2: plan → inline approval card with four chained tasks.
  await ask(page, 'Plan how to understand this Markov chain.')
  let card = approval(page, 'Create roadmap')
  await expect(card).toBeVisible()
  await expect(card.locator('.agent-task-title')).toHaveText(MARKOV)
  await expect(card.locator('.agent-task-after')).toHaveText(MARKOV.slice(0, 3).map(title => ` after ${title}`))
  await expect(card.getByText(/Bound to roadmap revision/)).toHaveCount(0)
  expect(await roadmaps(request, project)).toEqual([])
  await capture(page, 'plan-approval', card.getByRole('button', { name: 'Approve', exact: true }))

  // Mobile: the stream does not scroll horizontally and the approval buttons sit above the composer.
  await page.setViewportSize(mobile)
  await page.waitForFunction(width => document.documentElement.clientWidth === width, mobile.width)
  // rAF polling runs before ResizeObserver in a frame: wait until the stream's scroll reserve
  // (set by the composer's ResizeObserver) matches the composer height at this width.
  await page.waitForFunction(() => {
    const shell = document.querySelector<HTMLElement>('.composer-shell'), column = shell?.closest<HTMLElement>('.notebook-column')
    return !!shell && !!column && Math.abs(parseFloat(column.style.getPropertyValue('--composer-reserve')) - shell.getBoundingClientRect().height) < 0.5
  })
  const approveButton = card.getByRole('button', { name: 'Approve', exact: true })
  // Scrolling it to the end of the stream must leave it above the floating composer (scroll-padding reserve).
  await approveButton.evaluate(element => element.scrollIntoView({ block: 'end' }))
  const overflow = await page.evaluate(() => [document.documentElement, document.querySelector('.notebook-editor')!]
    .map(element => element.scrollWidth - element.clientWidth))
  expect(overflow.every(extra => extra <= 0)).toBe(true)
  const buttonBox = (await approveButton.boundingBox())!, composerBox = (await page.locator('.composer-shell').boundingBox())!
  expect(buttonBox.y + buttonBox.height).toBeLessThanOrEqual(composerBox.y)
  await capture(page, 'plan-approval', approveButton, mobile)
  await page.setViewportSize(desktop)

  // J7: the pending approval is persisted, not page state.
  await page.reload()
  await expect(approval(page, 'Create roadmap')).toBeVisible()
  await page.getByRole('link', { name: 'Europa', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  await openNotebook(page, notebook)
  card = approval(page, 'Create roadmap')
  await expect(card.locator('.agent-task-title')).toHaveText(MARKOV)

  await approve(page, card)
  await expect(coordinator(page).filter({ hasText: 'Saved the roadmap with 4 tasks. Accept tasks in the Roadmap panel as you verify them.' })).toHaveCount(1)
  // The Roadmap panel updates without a reload.
  await expect(roadmapTasks(page)).toHaveCount(4)
  await expect(roadmapTasks(page).locator('.task-state')).toHaveText(['upcoming', 'upcoming', 'upcoming', 'upcoming'])
  await expect(page.locator('#roadmap .roadmap-title strong')).toHaveText('0%')
  let [roadmap] = await roadmaps(request, project)
  expect(roadmap.revision).toBe(1)
  expect(roadmap.data.tasks.map(task => task.status)).toEqual(['pending', 'pending', 'pending', 'pending'])
  const ids = roadmap.data.tasks.map(task => task.id)
  await expect(runSummaries(page)).toHaveText(['Completed · 0 actions', 'Completed · 1 action'])
  await capture(page, 'plan-saved', coordinator(page).last())

  // J3: revision bound to revision 1, existing task IDs kept.
  await ask(page, 'Add a step to compare with a 3-state chain.')
  card = approval(page, 'Revise roadmap')
  await expect(card).toBeVisible()
  await expect(card.getByText('Bound to roadmap revision 1')).toBeVisible()
  await expect(card.locator('.agent-task-title')).toHaveText([...MARKOV, 'Compare with a 3-state chain'])
  await capture(page, 'revise-approval', card.getByRole('button', { name: 'Approve', exact: true }))
  await capture(page, 'revise-approval', card.getByRole('button', { name: 'Approve', exact: true }), mobile)
  await approve(page, card)
  await expect(coordinator(page).filter({ hasText: 'Saved the roadmap with 5 tasks.' })).toHaveCount(1)
  await expect(roadmapTasks(page)).toHaveCount(5)
  ;[roadmap] = await roadmaps(request, project)
  expect(roadmap.revision).toBe(2)
  expect(roadmap.data.tasks.slice(0, 4).map(task => task.id)).toEqual(ids)
  expect(roadmap.data.tasks.map(task => task.status)).toEqual(Array(5).fill('pending'))

  // J5: status comes from read_roadmap; nothing is claimed done.
  await ask(page, "What's left and why isn't it done?")
  const status = coordinator(page).filter({ hasText: 'Roadmap revision 2 (0% complete):' })
  await expect(status).toHaveCount(1)
  await expect(status).toContainText('- Define a transition matrix: pending')
  await expect(status).toContainText('- Compare with a 3-state chain: pending — waits for Visualize convergence')
  await expect(status).not.toContainText(': completed')

  // J7: after reload every finished run panel is collapsed with its action count.
  await page.reload()
  await expect(runSummaries(page)).toHaveText(['Completed · 0 actions', 'Completed · 1 action', 'Completed · 1 action', 'Completed · 1 action'])
  for (const details of await page.locator('.agent-stream details.agent-run').all()) await expect(details).not.toHaveAttribute('open', '')
  await expect(page.getByRole('region', { name: /^Approval:/ })).toHaveCount(0)
  await expect(roadmapTasks(page)).toHaveCount(5)
  await capture(page, 'completed', coordinator(page).last())
  await capture(page, 'completed', coordinator(page).last(), mobile)
})

test('J4 conflict: an owner roadmap edit while a revision is pending wins', async ({ page, request }) => {
  const { notebook, project } = await projectNotebook(request, 'Conflict Study 4C')
  const created = await request.post('/api/roadmaps', { data: { operation_id: operation(), project_id: project, notebook_id: notebook,
    objective: 'Owner plan', tasks: [{ id: 'own-4c-task', title: 'Owner task', acceptance_criteria: ['Checked by the owner'] }] } })
  expect(created.status()).toBe(201)
  const original = await created.json()
  await openNotebook(page, notebook)
  await ask(page, 'Revise the roadmap')
  const card = approval(page, 'Revise roadmap')
  await expect(card.getByText('Bound to roadmap revision 1')).toBeVisible()
  const edited = await request.put(`/api/roadmaps/${original.id}`, { data: { operation_id: operation(), revision: 1,
    objective: 'Owner objective edited during review', tasks: [{ id: 'own-4c-task', title: 'Owner task', acceptance_criteria: ['Checked by the owner'] }] } })
  expect(edited.ok()).toBe(true)
  await approve(page, card)
  await expect(coordinator(page).filter({ hasText: 'The plan was not saved: The roadmap changed after this plan was proposed' })).toHaveCount(1)
  const [roadmap] = await roadmaps(request, project)
  expect(roadmap.revision).toBe(2)
  expect(roadmap.data.objective).toBe('Owner objective edited during review')
  expect(roadmap.data.tasks.map(task => task.id)).toEqual(['own-4c-task'])
  await expect(page.locator('#roadmap .roadmap-title h3')).toHaveText('Owner objective edited during review')
  await capture(page, 'conflict', coordinator(page).last())
})

test('J6 standalone notebook: planning is refused honestly and discussion still works', async ({ page, request }) => {
  const created = await request.post('/api/notebooks', { data: { title: 'Loose 4C' } })
  expect(created.status()).toBe(201)
  const notebook = (await created.json()).metadata.europa.notebook_id as string
  await openNotebook(page, notebook)
  await ask(page, 'Plan how to understand this Markov chain.')
  await expect(coordinator(page).filter({ hasText: 'Planning needs a project.' })).toHaveCount(1)
  await expect(page.getByRole('region', { name: /^Approval:/ })).toHaveCount(0)
  await ask(page, 'What is in here?')
  await expect(coordinator(page).filter({ hasText: 'It is not in a project, so planning needs a project first.' })).toHaveCount(1)
  await expect(runSummaries(page)).toHaveText(['Completed · 0 actions', 'Completed · 0 actions'])
  await capture(page, 'standalone', coordinator(page).last())
})
