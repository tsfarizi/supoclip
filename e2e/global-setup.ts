/**
 * Global setup for the SupoClip E2E suite.
 *
 * Responsibilities (contract: this must pass before any spec runs):
 *  1. Health-check the native stack (backend :8000, frontend :3107). On
 *     failure the run aborts with a message pointing at run.ps1.
 *  2. Provision a login user idempotently via better-auth REST on the
 *     FRONTEND (no Prisma, no backend signing needed):
 *       - preferred: the existing fixture user e2e-user@supoclip.test
 *         (owns the completed "Ep. 5 BOXABL" task with 4 clips)
 *       - fallback: if that user does not exist (401), sign up a fresh
 *         suite user `e2e-suite-<timestamp>@supoclip.test`
 *     Credentials are persisted to .credentials.json for the specs.
 *  3. Record fixture availability (completed task present or not) to
 *     .fixtures.json so detail/share specs can skip with a reason.
 *  4. Capture the better-auth session cookie from the provisioning sign-in
 *     and persist it as storageState.json. Auth-required specs reuse this
 *     session via test.use({ storageState }) — this keeps the suite fast AND
 *     avoids better-auth's sign-in rate limiter (429 "Too many requests"),
 *     which blocks repeated UI sign-ins within a short window.
 *
 * This setup does NOT touch the database and does NOT delete or modify the
 * fixture user's data.
 */

import { mkdir, writeFile } from "fs/promises";
import path from "path";

const BACKEND_URL = "http://localhost:8000";
const FRONTEND_URL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3107";

const FIXTURE_USER_EMAIL = "e2e-user@supoclip.test";
const FIXTURE_USER_PASSWORD = "Password123!";

const FIXTURE_TASK_TITLE =
  "Ep. 5 BOXABL Factory Update 2023 - Panel Upgrades, Factory 3, and BOXZILLA";

interface ParsedCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  expires?: number;
  httpOnly: boolean;
  secure: boolean;
  sameSite: "Lax" | "Strict" | "None";
}

function parseSetCookie(header: string): ParsedCookie | null {
  const parts = header.split(";").map((part) => part.trim());
  const first = parts.shift();
  if (!first) return null;
  const eq = first.indexOf("=");
  if (eq <= 0) return null;

  const cookie: ParsedCookie = {
    name: first.slice(0, eq),
    value: first.slice(eq + 1),
    domain: "localhost",
    path: "/",
    httpOnly: false,
    secure: false,
    sameSite: "Lax",
  };

  for (const part of parts) {
    const [rawKey, rawValue] = part.split("=");
    const key = rawKey.trim().toLowerCase();
    const value = (rawValue || "").trim();
    if (key === "domain" && value) cookie.domain = value.replace(/^\./, "");
    if (key === "path" && value) cookie.path = value;
    if (key === "httponly") cookie.httpOnly = true;
    if (key === "secure") cookie.secure = true;
    if (key === "samesite" && value) {
      const normalized = value.toLowerCase();
      if (normalized === "strict") cookie.sameSite = "Strict";
      else if (normalized === "none") cookie.sameSite = "None";
      else cookie.sameSite = "Lax";
    }
    if (key === "max-age" && value) {
      const maxAge = Number(value);
      if (Number.isFinite(maxAge) && maxAge > 0) {
        cookie.expires = Math.floor(Date.now() / 1000) + maxAge;
      }
    }
  }
  return cookie;
}

async function backendHealth(): Promise<void> {
  let ok = false;
  try {
    const res = await fetch(`${BACKEND_URL}/health`);
    const body = (await res.json().catch(() => null)) as {
      status?: string;
    } | null;
    ok = res.ok && body?.status === "healthy";
  } catch {
    ok = false;
  }
  if (!ok) {
    throw new Error(
      "Backend health check failed at http://localhost:8000/health. " +
        "Jalankan .\\run.ps1 dulu (backend + frontend + Redis) sebelum menjalankan suite E2E.",
    );
  }
}

async function frontendHealth(): Promise<void> {
  let ok = false;
  try {
    const res = await fetch(`${FRONTEND_URL}/`);
    ok = res.ok;
  } catch {
    ok = false;
  }
  if (!ok) {
    throw new Error(
      `Frontend health check failed at ${FRONTEND_URL}/. ` +
        "Jalankan .\\run.ps1 dulu (frontend production build di :3107) sebelum menjalankan suite E2E.",
    );
  }
}

async function fetchWithRateLimitRetry(
  url: string,
  init: RequestInit,
  maxRetries = 2,
): Promise<Response> {
  // better-auth rate-limits /sign-in and /sign-up to 3 requests per 10 s per
  // client IP. A previous run (or manual probing) may still occupy the window,
  // so a 429 is retried after the window expires.
  let lastResponse: Response | null = null;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    if (attempt > 0) {
      await new Promise((resolve) => setTimeout(resolve, 11_000));
    }
    const response = await fetch(url, init);
    if (response.status !== 429) return response;
    lastResponse = response;
  }
  return lastResponse!;
}

async function provisionUser(): Promise<{
  email: string;
  password: string;
  source: "existing-e2e-user" | "created-suite-user";
  fixtureTaskAvailable: boolean;
  sessionCookies: ParsedCookie[];
}> {
  // better-auth (frontend) rejects requests without an Origin header
  // (CSRF protection) — a real browser always sends one.
  const headers = {
    "Content-Type": "application/json",
    Origin: FRONTEND_URL,
  };
  const extractCookies = (response: Response): ParsedCookie[] =>
    (typeof response.headers.getSetCookie === "function"
      ? response.headers.getSetCookie()
      : [response.headers.get("set-cookie") || ""].filter(Boolean)
    )
      .map(parseSetCookie)
      .filter((cookie): cookie is ParsedCookie => Boolean(cookie));

  // 1) Try the existing fixture user first (idempotent: no user creation when
  //    it already exists, no password reset, no data mutation).
  const signInRes = await fetchWithRateLimitRetry(
    `${FRONTEND_URL}/api/auth/sign-in/email`,
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        email: FIXTURE_USER_EMAIL,
        password: FIXTURE_USER_PASSWORD,
      }),
    },
  );

  if (signInRes.ok) {
    return {
      email: FIXTURE_USER_EMAIL,
      password: FIXTURE_USER_PASSWORD,
      source: "existing-e2e-user",
      fixtureTaskAvailable: true,
      sessionCookies: extractCookies(signInRes),
    };
  }

  if (signInRes.status === 401) {
    // Fixture user is gone or password changed — create a dedicated suite
    // user for this run. It has NO completed fixture task.
    const email = `e2e-suite-${Date.now()}@supoclip.test`;
    const signUpRes = await fetchWithRateLimitRetry(
      `${FRONTEND_URL}/api/auth/sign-up/email`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({
          email,
          password: FIXTURE_USER_PASSWORD,
          name: "E2E Suite User",
        }),
      },
    );
    if (!signUpRes.ok) {
      const body = await signUpRes.text().catch(() => "");
      throw new Error(
        `Sign-up for suite user failed with ${signUpRes.status}: ${body.slice(0, 300)}`,
      );
    }
    return {
      email,
      password: FIXTURE_USER_PASSWORD,
      source: "created-suite-user",
      fixtureTaskAvailable: false,
      sessionCookies: extractCookies(signUpRes),
    };
  }

  throw new Error(
    `Sign-in probe returned unexpected status ${signInRes.status}. ` +
      "Cannot provision an E2E user; fix auth setup before running the suite.",
  );
}

export default async function globalSetup(): Promise<void> {
  await backendHealth();
  await frontendHealth();

  const user = await provisionUser();

  await mkdir(process.cwd(), { recursive: true });
  await writeFile(
    path.join(process.cwd(), ".credentials.json"),
    JSON.stringify(
      {
        email: user.email,
        password: user.password,
        source: user.source,
        fixtureTaskAvailable: user.fixtureTaskAvailable,
      },
      null,
      2,
    ),
  );
  await writeFile(
    path.join(process.cwd(), ".fixtures.json"),
    JSON.stringify(
      {
        fixtureTaskAvailable: user.fixtureTaskAvailable,
        fixtureTaskTitle: FIXTURE_TASK_TITLE,
        note:
          user.source === "existing-e2e-user"
            ? "Completed fixture task assumed present; specs locate it in the UI and skip when absent."
            : "Fresh suite user created for this run; no completed fixture task — detail/share specs skip with a reason.",
      },
      null,
      2,
    ),
  );

  // Storage state: pre-authenticated session for auth-required specs.
  if (user.sessionCookies.length === 0) {
    throw new Error(
      "Provisioning sign-in succeeded but no session cookie was returned — " +
        "cannot build storageState; fix auth setup.",
    );
  }
  await writeFile(
    path.join(process.cwd(), "storageState.json"),
    JSON.stringify(
      {
        cookies: user.sessionCookies,
        origins: [],
      },
      null,
      2,
    ),
  );

  console.log(
    `[global-setup] user=${user.source} email=${user.email} ` +
      `fixtureTaskAvailable=${user.fixtureTaskAvailable} cookies=${user.sessionCookies.length}`,
  );
}
