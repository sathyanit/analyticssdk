const { defineConfig } = require('@playwright/test');
require('node:fs').mkdirSync('artifacts', { recursive: true });
module.exports = defineConfig({
  testDir: './tests/browser',
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8001',
    channel: 'chrome',
    headless: true,
    trace: 'retain-on-failure'
  },
  webServer: {
    command: `${process.platform === 'win32' ? '.\\.venv\\Scripts\\python.exe' : '.venv/bin/python'} -m uvicorn run:app --host 127.0.0.1 --port 8001`,
    url: 'http://127.0.0.1:8001/health',
    reuseExistingServer: false,
    env: {
      DATABASE_PATH: `artifacts/browser-${Date.now()}.db`,
      ALLOWED_ORIGINS: 'http://127.0.0.1:8001',
      DASHBOARD_TOKEN: 'browser-test-token',
      ANALYTICS_SERVER_KEY: 'browser-test-server-key'
    }
  }
});
