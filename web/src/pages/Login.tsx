import { useState } from "react";
import type { FormEvent } from "react";
import { login } from "../api";
import { ErrorBanner } from "../components";

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password, totp.trim());
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось войти");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="card login-card">
        <div className="card-body">
          <div className="brand" style={{ padding: 0, marginBottom: 16 }}>
            <div className="brand-mark" aria-hidden="true" />
            <div>
              <div className="brand-name">IPTV Control Center</div>
              <div className="brand-sub">Control plane</div>
            </div>
          </div>
          <h1 className="login-title">Вход</h1>
          <p className="muted login-sub">Пароль и одноразовый код из приложения-аутентификатора.</p>
          {error && <ErrorBanner message={error} />}
          <form onSubmit={submit}>
            <div className="field">
              <label htmlFor="email">Email</label>
              <input
                id="email"
                className="input"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="password">Пароль</label>
              <input
                id="password"
                className="input"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="totp">Код TOTP</label>
              <input
                id="totp"
                className="input"
                inputMode="numeric"
                pattern="[0-9 ]*"
                maxLength={8}
                value={totp}
                onChange={(event) => setTotp(event.target.value)}
                required
              />
            </div>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy ? "Проверка…" : "Войти"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
