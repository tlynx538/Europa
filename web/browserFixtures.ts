import { test as base, expect } from '@playwright/test'
export * from '@playwright/test'

const knownNotebooks = new Set<string>(['default'])

// The combined suite owns its API and storage. Reap runtimes between tests,
// retaining the accumulated catalog; individual journeys still verify survival
// across Home/Close/history. Never run this cleanup against a VM/owner preview.
export const test = base.extend<{ disposableRuntimeCleanup: void }>({
  disposableRuntimeCleanup: [async ({ request, baseURL }, use) => {
    await use()
    if (!process.env.EUROPA_ACCEPTANCE_ROOT || process.env.EUROPA_VM_ONLY === '1' || baseURL !== `http://127.0.0.1:${process.env.EUROPA_TEST_PORT || '18000'}`) return
    const response = await request.get('/api/notebooks')
    expect(response.ok()).toBe(true)
    for (const notebook of await response.json()) knownNotebooks.add(notebook.notebook_id)
    for (const id of knownNotebooks) {
      const stopped = await request.delete(`/api/runtime?notebook_id=${encodeURIComponent(id)}`)
      expect(stopped.status()).toBe(204)
    }
  }, { auto: true }],
})
