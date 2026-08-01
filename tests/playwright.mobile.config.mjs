import { defineConfig } from "@playwright/test";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const python = process.platform === "win32"
  ? join(repositoryRoot, ".venv", "Scripts", "python.exe")
  : "python";
const testPort = Number(process.env.MOVIESEATFINDER_TEST_PORT || 18765);

export default defineConfig({
  testDir: ".",
  testMatch: "mobile.integration.spec.mjs",
  fullyParallel: false,
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${testPort}`,
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  },
  webServer: {
    command: `"${python}" -m uvicorn app:app --host 127.0.0.1 --port ${testPort}`,
    url: `http://127.0.0.1:${testPort}`,
    reuseExistingServer: false,
  },
});
