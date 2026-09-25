/**
 * Apply Assistant helpers. The assistant never automates anything on a job site: it only puts prepared values on the
 * user's clipboard and opens pages in the user's own browser.
 */

const POPUP_NAME = "jobapplier-assist";
const POPUP_FEATURES = "popup,width=440,height=900";

/** Opens (or re-targets) the pop-out assistant window. Falls back to same-window navigation if popups are blocked. */
export function openAssistant(appId: number, navigate: (hash: string) => void): void {
  let w: Window | null = null;
  try {
    w = window.open(`#assist/${appId}`, POPUP_NAME, POPUP_FEATURES);
  } catch {
    w = null;
  }
  if (w) {
    try {
      w.focus();
    } catch {
      /* ignore */
    }
  } else {
    navigate(`#assist/${appId}`);
  }
}

/** From inside the pop-out: show a page in the main app window if it is still open, else navigate here. */
export function openInMainWindow(hash: string): void {
  try {
    const opener = window.opener as Window | null;
    if (opener && !opener.closed && opener.location.origin === window.location.origin) {
      opener.location.hash = hash;
      opener.focus();
      return;
    }
  } catch {
    /* cross-origin or closed opener: fall through */
  }
  window.location.hash = hash;
}

function execCommandCopy(text: string): boolean {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.setAttribute("aria-hidden", "true");
  ta.style.position = "fixed";
  ta.style.top = "-1000px";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  const prevFocus = document.activeElement as HTMLElement | null;
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  ta.value = ""; // do not leave the value in the DOM
  ta.remove();
  prevFocus?.focus?.();
  return ok;
}

/** Copies plain text: Clipboard API first, then the hidden-textarea fallback. */
export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      /* fall back below */
    }
  }
  if (!execCommandCopy(text)) throw new Error("Copy failed: your browser blocked clipboard access");
}

/**
 * Copies a value that must first be fetched (a revealed sensitive answer). The fetch runs once. ClipboardItem with a
 * promise keeps the user's click "activation" alive across the network call in browsers that require it (Safari).
 * The value is never stored or rendered by the caller.
 */
export async function copyAsync(getText: () => Promise<string>): Promise<void> {
  const pending = getText();
  pending.catch(() => undefined); // handled below; avoid an unhandled-rejection warning
  if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({ "text/plain": pending.then((t) => new Blob([t], { type: "text/plain" })) }),
      ]);
      return;
    } catch {
      /* fall through to the text path (reuses the same fetch) */
    }
  }
  const text = await pending;
  await copyText(text);
}

const STORAGE_PREFIX = "ja-assist-";

/** Per-application open-item checklist (non-sensitive: question keys only). Storage failures are ignored. */
export function loadChecklist(appId: number): string[] {
  try {
    const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${appId}`);
    const v: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function saveChecklist(appId: number, checked: string[]): void {
  try {
    window.localStorage.setItem(`${STORAGE_PREFIX}${appId}`, JSON.stringify(checked));
  } catch {
    /* private mode / storage disabled: checklist just isn't remembered */
  }
}
