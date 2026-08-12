/**
 * User settings: preference controls and the API-key lifecycle
 * (create → one-time plaintext → listed → revoke → revoked badge).
 *
 * Preferences are saved with the values already shown (no data mutation).
 * Starts authenticated via the shared storageState (see global-setup).
 */

import { expect, test } from "@playwright/test";

import { getStorageStatePath } from "./helpers";

test.use({ storageState: getStorageStatePath() });

test.describe("settings", () => {
  test("preference controls render and save without errors", async ({ page }) => {
    await page.goto("/settings");

    await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
    await expect(page.getByText("Default Font Settings")).toBeVisible();

    // Font Family select + Font Size slider + Font Color picker.
    await expect(page.getByText("Font Family")).toBeVisible();
    await expect(page.getByText(/font size: \d+px/i)).toBeVisible();
    await expect(page.getByRole("slider")).toBeVisible();
    await expect(page.getByText("Font Color")).toBeVisible();
    await expect(page.locator('input[type="color"]')).toBeVisible();

    // Completion emails toggle + save button.
    await expect(page.getByText("Completion emails")).toBeVisible();
    await expect(page.getByRole("switch")).toBeVisible();

    // Save with the current values — exercises the PATCH round-trip without
    // mutating the fixture user's preferences.
    await page.getByRole("button", { name: /save preferences/i }).click();
    await expect(page.getByText("Preferences saved successfully!")).toBeVisible();
  });

  test("API key lifecycle: create → one-time reveal → listed → revoke", async ({
    page,
  }) => {
    await page.goto("/settings/api-keys");

    await expect(page.getByRole("heading", { name: /api keys/i })).toBeVisible();

    const keyName = `e2e-suite-key-${Date.now()}`;
    await page.locator("#key-name").fill(keyName);
    await page.getByRole("button", { name: /create/i }).click();

    // One-time plaintext reveal.
    await expect(
      page.getByText(/copy your new key now — it won't be shown again/i),
    ).toBeVisible();
    const revealedCode = page.locator("code");
    await expect(revealedCode).not.toBeEmpty();

    // The key is listed with its name.
    const keyEntry = page
      .locator("div.flex.items-center.justify-between.p-4")
      .filter({ hasText: keyName });
    await expect(keyEntry).toHaveCount(1);
    await expect(keyEntry.getByText(/sk_/i)).toBeVisible();

    // Revoke → the entry shows a Revoked badge and no longer offers Revoke.
    await keyEntry.getByRole("button", { name: /revoke/i }).click();
    await expect(keyEntry.getByText("Revoked")).toBeVisible();
    await expect(keyEntry.getByRole("button", { name: /revoke/i })).toHaveCount(0);
  });
});
