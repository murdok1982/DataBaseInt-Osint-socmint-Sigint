/**
 * Accesibilidad WCAG 2.2 nivel AA (SPEC-05 §6) sobre las pantallas reales:
 * login y dashboard autenticado. axe-core analiza el DOM renderizado en
 * Chromium; cualquier violación de las reglas WCAG 2.0/2.1/2.2 A y AA falla la
 * prueba. Los datos de la API se simulan igual que en auth.spec.ts.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

const TOKEN = { access_token: "e2e-access", token_type: "bearer", expires_in: 900 };
const ANALYST = { id: "u-1", username: "ana.lopez", role: "ANALYST" };
const WCAG_22_AA = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

function json(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function anonymousSession(page: Page): Promise<void> {
  await page.route("**/api/v1/auth/refresh", (route) =>
    json(route, 401, { error: { code: "UNAUTHORIZED", message: "Sin sesión" } }),
  );
}

async function expectNoWcagViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_22_AA).analyze();
  const summary = results.violations.flatMap((v) =>
    v.nodes.map((n) => `${v.id} (${v.impact}) ${n.target.join(" ")} :: ${n.failureSummary ?? ""}`),
  );
  expect(summary, "violaciones WCAG 2.2 AA").toEqual([]);
}

test("la pantalla de login cumple WCAG 2.2 AA", async ({ page }) => {
  await anonymousSession(page);

  await page.goto("/login");
  await expect(page.getByLabel("Operador")).toBeVisible();

  await expectNoWcagViolations(page);
});

test("cada vista del dashboard autenticado cumple WCAG 2.2 AA", async ({ page }) => {
  await page.route("**/api/v1/**", (route) => json(route, 404, { error: { code: "NOT_FOUND", message: "mock" } }));
  await anonymousSession(page);
  await page.route("**/api/v1/auth/login", (route) => json(route, 200, TOKEN));
  await page.route("**/api/v1/auth/me", (route) => json(route, 200, ANALYST));

  await page.goto("/login");
  await page.getByLabel("Operador").fill("ana.lopez");
  await page.getByLabel("Contraseña").fill("contraseña-de-prueba");
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page.getByText("ana.lopez")).toBeVisible();

  // SPEC-05 §0: cada pestaña de la navegación principal es una vista auditable.
  const views = page.getByRole("navigation", { name: "Vistas del dashboard" }).getByRole("button");
  const count = await views.count();
  expect(count).toBeGreaterThan(0);
  for (let i = 0; i < count; i += 1) {
    await views.nth(i).click();
    await expect(views.nth(i)).toHaveAttribute("aria-current", "page");
    await expectNoWcagViolations(page);
  }
});

/** WCAG 2.2 1.4.10 (reflujo): a 320 px de ancho no hay desplazamiento horizontal. */
test("el login no tiene desplazamiento horizontal a 320 px (1.4.10)", async ({ page }) => {
  await anonymousSession(page);
  await page.setViewportSize({ width: 320, height: 640 });

  await page.goto("/login");
  await expect(page.getByLabel("Operador")).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, "píxeles de desplazamiento horizontal").toBeLessThanOrEqual(0);
});

/** WCAG 2.2 2.4.7 (foco visible): el foco por teclado tiene un indicador visible. */
test("el foco por teclado del login tiene indicador visible (2.4.7)", async ({ page }) => {
  await anonymousSession(page);
  await page.goto("/login");

  await page.getByLabel("Operador").focus();
  await page.keyboard.press("Tab");
  await page.getByLabel("Operador").focus();

  const indicator = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el) return { outline: "none", shadow: "none" };
    const style = getComputedStyle(el);
    return { outline: `${style.outlineStyle} ${style.outlineWidth}`, shadow: style.boxShadow };
  });
  const visible = indicator.outline.split(" ")[0] !== "none" || indicator.shadow !== "none";
  expect(visible, `indicador de foco: ${JSON.stringify(indicator)}`).toBe(true);
});
