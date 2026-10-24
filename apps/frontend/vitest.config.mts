import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Pruebas unitarias del dashboard (SPEC-05). Entorno jsdom y alias `@/` igual
 * que `tsconfig.json`. Umbral del 80 % en el código de servicios, contexto y
 * utilidades (constitución: cobertura mínima 80 % en código nuevo).
 */
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/services/**/*.{ts,tsx}", "src/lib/**/*.{ts,tsx}", "src/context/**/*.{ts,tsx}"],
      exclude: ["**/*.test.{ts,tsx}"],
      thresholds: { lines: 80, functions: 80, statements: 80 },
    },
  },
});
