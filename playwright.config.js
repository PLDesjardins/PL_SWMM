const {defineConfig} = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/browser', workers: 1,
  use: {baseURL: 'http://127.0.0.1:8765', httpCredentials: process.env.PL_SWMM_E2E_HOSTED === '1' ? {username: 'swmm', password: 'browser-test-password-only'} : undefined, headless: true, launchOptions: {executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', args: ['--no-sandbox']}},
  webServer: {
    command: 'PL_SWMM_DATA_DIR=/tmp/pl-swmm-browser-test .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765',
    url: 'http://127.0.0.1:8765/api/health', reuseExistingServer: false,
    env: process.env.PL_SWMM_E2E_HOSTED === '1' ? {PL_SWMM_REQUIRE_AUTH: '1', PL_SWMM_PASSWORD: 'browser-test-password-only'} : {},
  },
});
