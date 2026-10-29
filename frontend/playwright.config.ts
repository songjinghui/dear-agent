import { defineConfig } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = fileURLToPath(new URL(".", import.meta.url));
const projectRoot = resolve(frontendRoot, "..");
const backendPython = resolve(projectRoot, "backend", ".venv", "Scripts", "python.exe");
const testDataDir = resolve(tmpdir(), `personal-companion-agent-e2e-${randomUUID()}`);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8011",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `"${backendPython}" -m uvicorn companion.main:app --app-dir backend --host 127.0.0.1 --port 8011`,
    cwd: projectRoot,
    env: { ...process.env, COMPANION_DATA_DIR: testDataDir },
    url: "http://127.0.0.1:8011/",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
