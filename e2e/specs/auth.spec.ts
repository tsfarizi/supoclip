/**
 * Authentication flows against better-auth on the frontend:
 * wrong password, correct sign-in, and sign-out.
 *
 * RATE-LIMIT CONTRACT (better-auth defaults): /sign-in and /sign-up are
 * limited to 3 requests per 10 seconds per client IP. This spec therefore
 * performs at most TWO UI sign-ins per run (wrong-password + the sign-out
 * test's own session), plus the single provisioning sign-in from
 * global-setup — exactly 3 requests, which keeps the suite deterministic.
 * The "valid credentials" test reuses the session that global-setup created
 * by signing in with those exact credentials (via storageState).
 *
 * The sign-out test uses its OWN UI session (not the shared storageState),
 * because signing out revokes that session — revoking the shared session
 * would break every later spec.
 */

import { expect, test } from "@playwright/test";

import {
  EMPTY_STORAGE_STATE,
  getStorageStatePath,
  readCredentials,
  signIn,
} from "./helpers";

test.describe("sign-in with wrong password", () => {
  test.use({ storageState: EMPTY_STORAGE_STATE });

  test("shows an inline error and stays on /sign-in", async ({ page }) => {
    await page.goto("/sign-in");
    await page.getByPlaceholder("Email").fill("e2e-user@supoclip.test");
    await page.getByPlaceholder("Password").fill("definitely-wrong");
    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page.getByText("Invalid email or password")).toBeVisible();
    await expect(page).toHaveURL(/\/sign-in/);
  });
});

test.describe("sign-in with valid credentials", () => {
  // The provisioning sign-in in global-setup used these exact credentials and
  // captured its session into storageState — reusing it verifies that those
  // credentials yield an authenticated home (no extra rate-limited request).
  test.use({ storageState: getStorageStatePath() });

  test("lands on the authenticated home with the user name visible", async ({ page }) => {
    const credentials = readCredentials();
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /create new clip/i }),
    ).toBeVisible();
    await expect(page.getByText(credentials.email)).toBeVisible();
  });
});

test.describe("sign-out", () => {
  test.use({ storageState: EMPTY_STORAGE_STATE });

  test("returns to the sign-in page", async ({ page }) => {
    const credentials = readCredentials();
    await signIn(page, credentials.email, credentials.password);

    await page.getByRole("button", { name: /sign out/i }).click();
    await page.waitForURL(/\/sign-in/);
    await expect(page.getByText("Sign in to your account")).toBeVisible();
    await expect(page.getByPlaceholder("Email")).toBeVisible();
  });
});
