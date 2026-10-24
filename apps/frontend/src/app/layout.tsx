/**
 * Root layout — SPEC-05 §1/§5 (Auth/sesión + dashboard dark).
 * Envuelve toda la app con <AuthProvider> (contexto JWT + rol del operador).
 */

import type { ReactElement } from "react";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AuthProvider } from "@/context/AuthContext";

import "./globals.css";

export const metadata: Metadata = {
  title: "OSINT Nexus — Dashboard de inteligencia",
  description:
    "Exploración del grafo de conocimiento, dossier de entidades e informes OSINT (SPEC-05).",
};

export default function RootLayout({ children }: { children: ReactNode }): ReactElement {
  return (
    <html lang="es" className="dark">
      <body className="min-h-screen bg-base-950 font-sans text-slate-100 antialiased">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
