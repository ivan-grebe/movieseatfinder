import { defineConfig } from "@playwright/test";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const python = process.platform === "win32"
  ? join(repositoryRoot, ".venv", "Scripts", "python.exe")
  : "python";

export default defineConfig({
  testDir: ".",
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
    command: `"${python}" -m uvicorn app:app --host 127.0.0.1 --port 8765`,
    url: "http://127.0.0.1:8765",
    reuseExistingServer: !process.env.CI,
  },
});
