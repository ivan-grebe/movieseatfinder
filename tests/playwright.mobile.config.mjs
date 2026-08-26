import { defineConfig } from "@playwright/test";
import path from "node:path";

const repositoryRoot = path.join(import.meta.dirname, "..");
let python = "python";
if (process.platform === "win32") {
  python = path.join(repositoryRoot, ".venv", "Scripts", "python.exe");
}

export default defineConfig({
  fullyParallel: false,
  testDir: ".",
  testMatch: "mobile.integration.spec.mjs",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:8765",
    hasTouch: true,
    isMobile: true,
    viewport: { height: 844, width: 390 },
  },
  webServer: {
    command: `"${python}" -m uvicorn backend.server:app --host 127.0.0.1 --port 8765`,
    cwd: repositoryRoot,
    reuseExistingServer: !process.env.CI,
    url: "http://127.0.0.1:8765",
  },
});
