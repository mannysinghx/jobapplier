import { useState, type FormEvent } from "react";
import { api, errorMessage, type Me } from "../api";
import { ErrorBox } from "./ui";

export function Login({ onLogin, notice }: { onLogin: (me: Me) => void; notice?: string | null }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await api.login(username.trim(), password, totp.trim());
      setPassword("");
      setTotp("");
      onLogin(me);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login" onSubmit={submit}>
        <h1 className="brand">jobApplier</h1>
        <p className="muted small">Self-hosted. Sign in with your local account and authenticator code.</p>
        {notice ? <div className="alert alert-info">{notice}</div> : null}
        <label className="field">
          <span className="field-label">Username</span>
          <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required maxLength={80} autoFocus />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required maxLength={200} />
        </label>
        <label className="field">
          <span className="field-label">Authenticator code (TOTP)</span>
          <input
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="\d{6}"
            maxLength={6}
            placeholder="6-digit code"
            value={totp}
            onChange={(e) => setTotp(e.target.value.replace(/\D/g, ""))}
            title="6-digit code from your authenticator app"
          />
          <span className="field-hint">Required for admin accounts (MFA).</span>
        </label>
        <ErrorBox error={error} />
        <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
