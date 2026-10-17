/**
 * Next.js config — OSINT Nexus dashboard (SPEC-05).
 *
 * El cliente API (`src/services/api.ts`) usa `NEXT_PUBLIC_API_BASE_URL`:
 *  - Si está definida (p. ej. https://api.osint-nexus.local/api/v1) se llama
 *    directamente (CORS gestionado por el backend).
 *  - Si NO está definida, el cliente llama a `/api/v1` (mismo origen) y este
 *    rewrite hace de proxy hacia el backend en desarrollo local.
 * En producción Nginx resuelve `/api/v1` antes de llegar a Next.
 */

/** @type {import('next').NextConfig} */
const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://localhost:8000";
const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL;

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  eslint: {
    dirs: ["src"],
  },
  async rewrites() {
    if (apiBase) {
      return [];
    }
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendOrigin}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
