import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errorMessage, setCsrfToken, setUnauthorizedHandler, type Controls, type Me } from "./api";
import { AppContext, type AppCtx } from "./components/context";
import { Login } from "./components/Login";
import { Badge, Loading } from "./components/ui";
import { AnswersPage } from "./pages/AnswersPage";
import { ApplicationDetailPage } from "./pages/ApplicationDetailPage";
import { ApplicationsPage } from "./pages/ApplicationsPage";
import { ControlsPage } from "./pages/ControlsPage";
import { FactsPage } from "./pages/FactsPage";
import { HandoffsPage } from "./pages/HandoffsPage";
import { PreferencesPage } from "./pages/PreferencesPage";
import { ProfilePage } from "./pages/ProfilePage";
import { SourcesPage } from "./pages/SourcesPage";

const TABS = [
  { id: "profile", label: "Setup / Profile" },
  { id: "facts", label: "Facts" },
  { id: "answers", label: "Answers" },
  { id: "preferences", label: "Preferences" },
  { id: "sources", label: "Sources" },
  { id: "jobs", label: "Jobs" },
  { id: "handoffs", label: "Handoffs" },
  { id: "controls", label: "Controls & Privacy" },
] as const;
type TabId = (typeof TABS)[number]["id"];

interface Route {
  tab: TabId;
  sub: string | null;
}

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [head, ...rest] = raw.split("/");
  const tab = (TABS.find((t) => t.id === head)?.id ?? "profile") as TabId;
  return { tab, sub: rest.length ? decodeURIComponent(rest.join("/")) : null };
}

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined = checking session
  const [loginNotice, setLoginNotice] = useState<string | null>(null);
  const [route, setRoute] = useState<Route>(parseHash);
  const [controls, setControls] = useState<Controls | undefined>(undefined);
  const [controlsError, setControlsError] = useState<string | null>(null);

  // Session bootstrap + global 401 handler.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setMe((prev) => {
        if (prev) setLoginNotice("Your session ended. Please sign in again.");
        return null;
      });
      setCsrfToken(null);
    });
    api.me().then(
      (m) => {
        setCsrfToken(m.csrf_token);
        setMe(m);
      },
      () => setMe(null),
    );
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const refreshControls = useCallback(() => {
    api.controls().then(
      (c) => {
        setControls(c);
        setControlsError(null);
      },
      (e: unknown) => setControlsError(errorMessage(e)),
    );
  }, []);

  // Keep the PAUSED banner current: on login, on tab change, and every 60s.
  useEffect(() => {
    if (!me) return;
    refreshControls();
    const t = window.setInterval(refreshControls, 60_000);
    return () => window.clearInterval(t);
  }, [me, refreshControls]);
  useEffect(() => {
    if (me) refreshControls();
  }, [route.tab, me, refreshControls]);

  const navigate = useCallback((hash: string) => {
    const h = hash.startsWith("#") ? hash : `#${hash}`;
    if (window.location.hash === h) setRoute(parseHash());
    else window.location.hash = h;
    window.scrollTo(0, 0);
  }, []);

  const ctx = useMemo<AppCtx | null>(
    () => (me ? { me, canWrite: me.role === "admin", controls, refreshControls, navigate } : null),
    [me, controls, refreshControls, navigate],
  );

  async function logout() {
    try {
      await api.logout();
    } catch {
      /* session may already be gone; fall through to the login screen */
    }
    setCsrfToken(null);
    setMe(null);
    setControls(undefined);
    setLoginNotice("Signed out.");
  }

  if (me === undefined) return <div className="login-wrap"><Loading what="Checking session" /></div>;
  if (me === null || !ctx) {
    return (
      <Login
        notice={loginNotice}
        onLogin={(m) => {
          setCsrfToken(m.csrf_token);
          setLoginNotice(null);
          setMe(m);
        }}
      />
    );
  }

  return (
    <AppContext.Provider value={ctx}>
      <header className="app-header">
        <div className="header-row">
          <a className="brand" href="#profile">jobApplier</a>
          <div className="header-user">
            <span className="user-name">{me.username}</span>
            <Badge tone={me.role === "admin" ? "accent" : "muted"}>{me.role}</Badge>
            <button className="btn btn-small" onClick={logout}>Log out</button>
          </div>
        </div>
        <nav className="tabs" aria-label="Sections">
          {TABS.map((t) => (
            <a key={t.id} href={`#${t.id}`} className={route.tab === t.id ? "tab active" : "tab"} aria-current={route.tab === t.id ? "page" : undefined}>
              {t.label}
            </a>
          ))}
        </nav>
      </header>
      {controls?.paused ? (
        <div className="paused-banner" role="alert">
          <strong>PAUSED</strong> — polling and submission are halted
          {controls.kill_switch_file ? " (kill-switch file present on the host)" : ""}
          {controls.reason ? `. Reason: ${controls.reason}` : ""}.{" "}
          <a href="#controls">Controls</a>
        </div>
      ) : null}
      {controlsError ? <div className="alert alert-warn page-alert">Could not load pause state: {controlsError}</div> : null}
      <main className="main">
        {route.tab === "profile" && <ProfilePage />}
        {route.tab === "facts" && <FactsPage />}
        {route.tab === "answers" && <AnswersPage />}
        {route.tab === "preferences" && <PreferencesPage />}
        {route.tab === "sources" && <SourcesPage />}
        {route.tab === "jobs" &&
          (route.sub && /^\d+$/.test(route.sub) ? <ApplicationDetailPage id={Number(route.sub)} /> : <ApplicationsPage />)}
        {route.tab === "handoffs" && <HandoffsPage />}
        {route.tab === "controls" && <ControlsPage />}
      </main>
    </AppContext.Provider>
  );
}
