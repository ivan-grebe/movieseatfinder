import { defineConfig } from "@playwright/test";

const python = process.platform === "win32" ? ".\\.venv\\Scripts\\python.exe" : "python";

export default defineConfig({
  testDir: "./tests",
  testMatch: "mobile.integration.spec.mjs",
  fullyParallel: false,
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:8765",
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  },
  webServer: {
    command: `${python} -m uvicorn app:app --host 127.0.0.1 --port 8765`,
    url: "http://127.0.0.1:8765",
    reuseExistingServer: !process.env.CI,
  },
});
