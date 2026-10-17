"use client";

/**
 * Login — portal de acceso seguro.
 * Autorizado por: SPEC-04 §2 (JWT 30 min + refresh cookie HttpOnly; TOTP
 * obligatorio para ADMIN, opcional para ANALYST) y SPEC-05 §1.
 * Traza a: `authLogin` (con `totp_code` opcional), `authMe` (vía AuthContext).
 */

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/context/AuthContext";
import { ApiError } from "@/services/api";

export default function LoginPage(): JSX.Element {
  const { login, status } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "authenticated") router.replace("/");
  }, [status, router]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login({
        username,
        password,
        totp_code: totp.trim() || undefined,
      });
      router.replace("/");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Credenciales inválidas o servidor no disponible.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden p-6">
      {/* Topología difusa de fondo (ref. visual §4) */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-30"
        style={{
          backgroundImage:
            "radial-gradient(circle at 20% 30%, rgba(34,211,238,0.18), transparent 45%), radial-gradient(circle at 80% 70%, rgba(167,139,250,0.16), transparent 45%)",
        }}
      />

      <form
        onSubmit={(event) => void handleSubmit(event)}
        className="relative w-full max-w-sm rounded-xl border border-base-700 bg-base-900/80 p-6 shadow-2xl backdrop-blur"
      >
        <div className="mb-4 flex items-center gap-2">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-neon-cyan" aria-hidden="true" />
          <h1 className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-200">
            OSINT Nexus
          </h1>
        </div>
        <p className="mb-4 text-xs text-slate-500">
          Acceso restringido a operadores autorizados · SPEC-04
        </p>

        <label className="mb-3 flex flex-col gap-1 text-xs text-slate-400">
          Operador
          <input
            type="text"
            autoComplete="username"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            className="rounded border border-base-600 bg-base-950 px-3 py-2 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none"
          />
        </label>

        <label className="mb-4 flex flex-col gap-1 text-xs text-slate-400">
          Contraseña
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="rounded border border-base-600 bg-base-950 px-3 py-2 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none"
          />
        </label>

        <label className="mb-4 flex flex-col gap-1 text-xs text-slate-400">
          Código 2FA (TOTP, si aplica)
          <input
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]*"
            maxLength={6}
            value={totp}
            onChange={(event) => setTotp(event.target.value)}
            placeholder="000000"
            className="rounded border border-base-600 bg-base-950 px-3 py-2 text-sm tracking-[0.3em] text-slate-100 focus:border-neon-cyan focus:outline-none"
          />
        </label>

        {error && (
          <p role="alert" className="mb-3 rounded border border-neon-crimson/40 bg-neon-crimson/10 p-2 text-[11px] text-neon-crimson">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded border border-neon-cyan/50 px-3 py-2 text-sm font-medium text-neon-cyan transition hover:bg-neon-cyan/10 disabled:opacity-40"
        >
          {submitting ? "Verificando…" : "Entrar"}
        </button>
      </form>
    </main>
  );
}
