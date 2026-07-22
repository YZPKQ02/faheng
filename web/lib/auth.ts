const AUTH_STORAGE_KEY = "legal-advisor-auth-session";
const AUTH_TRANSACTION_KEY = "legal-advisor-auth-transaction";

export type AuthSession = {
  accessToken: string;
  refreshToken?: string;
  idToken?: string;
  tokenType: string;
  expiresAt?: number;
  subject?: string;
};

type AuthTransaction = {
  state: string;
  nonce: string;
  codeVerifier: string;
  redirectUri: string;
};

type TokenResponse = {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  token_type?: string;
  expires_in?: number;
};

function browserStorage() {
  if (typeof window === "undefined") return null;
  return window.localStorage;
}

function authEnabled() {
  return process.env.NEXT_PUBLIC_AUTH_ENABLED === "true";
}

function nowSeconds() {
  return Math.floor(Date.now() / 1000);
}

function randomBase64Url(byteLength = 32) {
  const bytes = new Uint8Array(byteLength);
  window.crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

function base64Url(bytes: Uint8Array) {
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function codeChallenge(verifier: string) {
  const digest = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

function configuredRedirectUri() {
  if (process.env.NEXT_PUBLIC_OIDC_REDIRECT_URI) return process.env.NEXT_PUBLIC_OIDC_REDIRECT_URI;
  if (typeof window === "undefined") return "";
  return `${window.location.origin}${window.location.pathname}`;
}

function getTokenEndpoint() {
  return process.env.NEXT_PUBLIC_OIDC_TOKEN_ENDPOINT ?? "";
}

export function isAuthRequired() {
  return authEnabled();
}

export function isOidcConfigured() {
  return Boolean(
    process.env.NEXT_PUBLIC_OIDC_AUTHORIZATION_ENDPOINT
      && process.env.NEXT_PUBLIC_OIDC_TOKEN_ENDPOINT
      && process.env.NEXT_PUBLIC_OIDC_CLIENT_ID,
  );
}

export function getAuthSession(): AuthSession | null {
  const storage = browserStorage();
  if (!storage) return null;
  const raw = storage.getItem(AUTH_STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthSession;
  } catch {
    storage.removeItem(AUTH_STORAGE_KEY);
    return null;
  }
}

export function saveAuthToken(accessToken: string) {
  const trimmed = accessToken.trim().replace(/^Bearer\s+/i, "");
  if (!trimmed) throw new Error("Access token is required");
  saveAuthSession({ accessToken: trimmed, tokenType: "Bearer" });
}

export function saveAuthSession(session: AuthSession) {
  const storage = browserStorage();
  if (!storage) return;
  storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
}

export function clearAuthSession() {
  const storage = browserStorage();
  if (!storage) return;
  storage.removeItem(AUTH_STORAGE_KEY);
  storage.removeItem(AUTH_TRANSACTION_KEY);
}

function isExpired(session: AuthSession) {
  return typeof session.expiresAt === "number" && session.expiresAt <= nowSeconds() + 30;
}

async function refreshAuthSession(session: AuthSession): Promise<AuthSession | null> {
  if (!session.refreshToken || !getTokenEndpoint() || !process.env.NEXT_PUBLIC_OIDC_CLIENT_ID) return null;
  const body = new URLSearchParams({
    grant_type: "refresh_token",
    client_id: process.env.NEXT_PUBLIC_OIDC_CLIENT_ID,
    refresh_token: session.refreshToken,
  });
  const response = await fetch(getTokenEndpoint(), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!response.ok) return null;
  const payload = await response.json() as TokenResponse;
  const refreshed: AuthSession = {
    accessToken: payload.access_token,
    refreshToken: payload.refresh_token ?? session.refreshToken,
    idToken: payload.id_token ?? session.idToken,
    tokenType: payload.token_type ?? "Bearer",
    expiresAt: payload.expires_in ? nowSeconds() + payload.expires_in : undefined,
    subject: session.subject,
  };
  saveAuthSession(refreshed);
  return refreshed;
}

export async function getAccessToken() {
  const session = getAuthSession();
  if (!session) return null;
  if (!isExpired(session)) return session.accessToken;
  const refreshed = await refreshAuthSession(session);
  if (refreshed) return refreshed.accessToken;
  clearAuthSession();
  return null;
}

export async function authHeaders(): Promise<Record<string, string>> {
  const token = await getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function startOidcLogin() {
  const authorizationEndpoint = process.env.NEXT_PUBLIC_OIDC_AUTHORIZATION_ENDPOINT;
  const clientId = process.env.NEXT_PUBLIC_OIDC_CLIENT_ID;
  if (!authorizationEndpoint || !clientId) {
    throw new Error("OIDC login is not configured");
  }
  const redirectUri = configuredRedirectUri();
  const transaction: AuthTransaction = {
    state: randomBase64Url(16),
    nonce: randomBase64Url(16),
    codeVerifier: randomBase64Url(48),
    redirectUri,
  };
  browserStorage()?.setItem(AUTH_TRANSACTION_KEY, JSON.stringify(transaction));
  const params = new URLSearchParams({
    response_type: "code",
    client_id: clientId,
    redirect_uri: redirectUri,
    scope: process.env.NEXT_PUBLIC_OIDC_SCOPE ?? "openid profile email",
    state: transaction.state,
    nonce: transaction.nonce,
    code_challenge: await codeChallenge(transaction.codeVerifier),
    code_challenge_method: "S256",
  });
  if (process.env.NEXT_PUBLIC_OIDC_AUDIENCE) {
    params.set("audience", process.env.NEXT_PUBLIC_OIDC_AUDIENCE);
  }
  window.location.assign(`${authorizationEndpoint}?${params.toString()}`);
}

export async function completeOidcRedirect(url: string) {
  const parsed = new URL(url);
  const code = parsed.searchParams.get("code");
  const state = parsed.searchParams.get("state");
  if (!code || !state) return null;

  const storage = browserStorage();
  const raw = storage?.getItem(AUTH_TRANSACTION_KEY);
  if (!raw) throw new Error("OIDC transaction is missing");
  const transaction = JSON.parse(raw) as AuthTransaction;
  if (transaction.state !== state) throw new Error("OIDC state mismatch");

  const tokenEndpoint = getTokenEndpoint();
  const clientId = process.env.NEXT_PUBLIC_OIDC_CLIENT_ID;
  if (!tokenEndpoint || !clientId) throw new Error("OIDC token endpoint is not configured");

  const response = await fetch(tokenEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: clientId,
      code,
      redirect_uri: transaction.redirectUri,
      code_verifier: transaction.codeVerifier,
    }),
  });
  if (!response.ok) throw new Error("OIDC token exchange failed");
  const payload = await response.json() as TokenResponse;
  const session: AuthSession = {
    accessToken: payload.access_token,
    refreshToken: payload.refresh_token,
    idToken: payload.id_token,
    tokenType: payload.token_type ?? "Bearer",
    expiresAt: payload.expires_in ? nowSeconds() + payload.expires_in : undefined,
  };
  saveAuthSession(session);
  storage?.removeItem(AUTH_TRANSACTION_KEY);
  parsed.searchParams.delete("code");
  parsed.searchParams.delete("state");
  parsed.searchParams.delete("session_state");
  window.history.replaceState({}, document.title, `${parsed.pathname}${parsed.search}${parsed.hash}`);
  return session;
}
