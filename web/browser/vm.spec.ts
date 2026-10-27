import { test, expect } from '@playwright/test'

// The opt-in server uses the approved VM's existing bounded runc profile and
// cached image. Its loopback port is exposed only by Lima's existing forwarding.
test('browser executes and refreshes saved results on ARM64 runc', async ({ page }, info) => {
  test.skip(!process.env.EUROPA_VM_URL, 'requires explicit approved Lima probe server')
  await expect.poll(async () => { try { return (await page.request.get(`${process.env.EUROPA_VM_URL}/api/notebooks`)).ok() } catch { return false } }, { timeout: 30000 }).toBe(true)
  await page.goto(process.env.EUROPA_VM_URL!)
  page.once('dialog', d => d.accept('ARM64 browser proof'))
  await page.getByRole('button', { name: 'New notebook', exact: true }).click()
  await page.getByRole('button', { name: '+ Code' }).click()
  await page.locator('.cm-content').fill('import platform\nprint(platform.machine())\nprint(6*7)')
  await page.getByRole('button', { name: 'Run', exact: true }).click()
  await expect(page.locator('.cell .outputs')).toContainText('aarch64', { timeout: 60000 })
  await expect(page.locator('.cell .outputs')).toContainText('42')
  await expect(page.getByText(/Execution: applied \(ok\)/)).toBeVisible()
  await page.reload()
  await expect(page.locator('.cell .outputs')).toContainText('aarch64')
  await expect(page.locator('.cell .outputs')).toContainText('42')
  await page.screenshot({ path: info.outputPath('arm64.png'), fullPage: true })
})
