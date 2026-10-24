/**
 * Flujo de sesión del dashboard (SPEC-04 §2 y SPEC-05 §1): redirección al login,
 * login correcto y errores del contrato mostrados al operador.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

const TOKEN = { access_token: "e2e-access", token_type: "bearer", expires_in: 900 };
const ANALYST = { id: "u-1", username: "ana.lopez", role: "ANALYST" };

function json(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** Sin sesión: la cookie de refresh no existe. */
async function anonymousSession(page: Page): Promise<void> {
  await page.route("**/api/v1/auth/refresh", (route) =>
    json(route, 401, { error: { code: "UNAUTHORIZED", message: "Sin sesión" } }),
  );
}

test("sin sesión, el dashboard redirige al login", async ({ page }) => {
  await anonymousSession(page);

  await page.goto("/");

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByLabel("Operador")).toBeVisible();
  await expect(page.getByLabel("Contraseña")).toBeVisible();
});

test("un login correcto abre el dashboard con el operador identificado", async ({ page }) => {
  // Playwright da prioridad a la última ruta registrada: el comodín va primero.
  await page.route("**/api/v1/**", (route) => json(route, 404, { error: { code: "NOT_FOUND", message: "mock" } }));
  await anonymousSession(page);
  await page.route("**/api/v1/auth/login", (route) => json(route, 200, TOKEN));
  await page.route("**/api/v1/auth/me", (route) => json(route, 200, ANALYST));

  await page.goto("/login");
  await page.getByLabel("Operador").fill("ana.lopez");
  await page.getByLabel("Contraseña").fill("contraseña-de-prueba");
  await page.getByRole("button", { name: "Entrar" }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText("ana.lopez")).toBeVisible();
});

test("credenciales incorrectas muestran el mensaje del contrato", async ({ page }) => {
  await anonymousSession(page);
  await page.route("**/api/v1/auth/login", (route) =>
    json(route, 401, { error: { code: "UNAUTHORIZED", message: "Credenciales inválidas" } }),
  );

  await page.goto("/login");
  await page.getByLabel("Operador").fill("ana.lopez");
  await page.getByLabel("Contraseña").fill("mala");
  await page.getByRole("button", { name: "Entrar" }).click();

  await expect(page.locator("p[role=alert]")).toHaveText("Credenciales inválidas");
  await expect(page).toHaveURL(/\/login$/);
});

test("el bloqueo por fuerza bruta (429) se comunica al operador", async ({ page }) => {
  await anonymousSession(page);
  await page.route("**/api/v1/auth/login", (route) =>
    json(route, 429, { error: { code: "TOO_MANY_REQUESTS", message: "Demasiados intentos" } }),
  );

  await page.goto("/login");
  await page.getByLabel("Operador").fill("ana.lopez");
  await page.getByLabel("Contraseña").fill("mala");
  await page.getByRole("button", { name: "Entrar" }).click();

  await expect(page.locator("p[role=alert]")).toHaveText("Demasiados intentos");
});
