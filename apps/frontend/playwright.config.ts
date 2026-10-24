import { defineConfig, devices } from "@playwright/test";

/**
 * Pruebas e2e del dashboard (SPEC-05). La API se simula en el navegador con
 * `page.route`, así que no requieren backend ni base de datos.
 * En CI se ejecuta contra el build de producción (`npm run build` antes).
 */
const PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  // Un worker: con paralelismo, `page.goto` agotaba el tiempo contra el servidor
  // de `next start` (el rewrite a :8000 queda sin backend). Suite pequeña: aceptable.
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run start -- -p ${PORT}`,
    url: `http://127.0.0.1:${PORT}/login`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
