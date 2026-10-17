import type { Config } from "tailwindcss";

/**
 * Paleta derivada de `.antigravity/midjourney-ui-prompts.md`
 * (Palantir Gotham / Maltego / Recorded Future): dark mode, acentos neon.
 */
const config: Config = {
  darkMode: "class",
  content: [
    "./src/app/**/*.{ts,tsx}",
    "./src/components/**/*.{ts,tsx}",
    "./src/context/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        base: {
          950: "#05070d",
          900: "#0a0f1a",
          800: "#111827",
          700: "#1a2233",
          600: "#243049",
        },
        neon: {
          cyan: "#22d3ee",
          amber: "#fbbf24",
          crimson: "#ef4444",
          violet: "#a78bfa",
          emerald: "#34d399",
        },
      },
      fontFamily: {
        sans: ["system-ui", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(34,211,238,0.25), 0 0 24px -8px rgba(34,211,238,0.45)",
      },
    },
  },
  plugins: [],
};

export default config;
