import { defineConfig, devices } from '@playwright/test'

// Runs the real FastAPI backend and the Vite frontend together on dedicated ports.
// The backend runs with AI disabled by default so the E2E suite is deterministic and free;
// set E2E_USE_AI=true to exercise the live model configured in backend/.env: the provider is chosen by
// LLM_PROVIDER (default glm, which needs LLM_API_KEY and LLM_BASE_URL; or LLM_PROVIDER=anthropic with ANTHROPIC_API_KEY).
const useAI = process.env.E2E_USE_AI === 'true'
const python = process.env.E2E_PYTHON ?? (process.platform === 'win32' ? '.venv\\Scripts\\python' : '.venv/bin/python')
const BACKEND_PORT = 8001
const FRONTEND_PORT = 5174

export default defineConfig({
  testDir: './e2e',
  timeout: useAI ? 90_000 : 30_000,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
  webServer: [
    {
      command: `${python} -m uvicorn app.main:app --port ${BACKEND_PORT}`,
      cwd: '../backend',
      url: `http://127.0.0.1:${BACKEND_PORT}/api/health`,
      env: {
        AI_ENABLED: useAI ? 'true' : 'false',
        LOG_LEVEL: 'WARNING',
        // Both browser projects drive this one backend from one IP, far faster than a real guest.
        // Limits stay on (so a bug that spams the API still shows up) but well above the harness's rate.
        RATE_LIMIT_IP_PER_MINUTE: '600',
        RATE_LIMIT_IP_BURST: '200',
      },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${FRONTEND_PORT} --strictPort`,
      url: `http://127.0.0.1:${FRONTEND_PORT}`,
      env: { VITE_PROXY_TARGET: `http://127.0.0.1:${BACKEND_PORT}` },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
