import { defineConfig } from '@playwright/test'
import workspace from './playwright.workspace.config'
export default defineConfig({ ...workspace, testDir: './browser-dashboard-c' })
