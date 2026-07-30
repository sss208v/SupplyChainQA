import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  retries: 0,
  workers: 1,
  timeout: 30000,
  expect: { timeout: 10000 },
  reporter: [
    ['list'],
    ['html', { outputFolder: '../e2e-report', open: 'never' }],
  ],

  use: {
    baseURL: 'http://127.0.0.1:4000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    headless: true,
    viewport: { width: 1440, height: 900 },
  },

  projects: [
    // 1. Setup: login once, save storageState
    {
      name: 'setup',
      testMatch: /auth-setup\.setup\.js/,
    },
    // 2. All tests: use saved storageState
    {
      name: 'chromium',
      dependencies: ['setup'],
      use: {
        storageState: 'tests/.auth/user.json',
      },
    },
  ],
})
