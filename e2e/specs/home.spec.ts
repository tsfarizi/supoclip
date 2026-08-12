/**
 * Authenticated home: header identity, create-clip form controls, submit
 * validation, and the latest-generation task card.
 *
 * Starts authenticated via the shared storageState (see global-setup).
 */

import { expect, test } from "@playwright/test";

import { getStorageStatePath, readCredentials, readFixtures } from "./helpers";

test.use({ storageState: getStorageStatePath() });

test.describe("home (authenticated)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /create new clip/i }),
    ).toBeVisible();
  });

  test("header shows the signed-in user and primary actions", async ({ page }) => {
    const credentials = readCredentials();
    await expect(page.getByText(credentials.email)).toBeVisible();
    await expect(page.getByRole("link", { name: /all generations/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible();
  });

  test("create form exposes all source/style controls", async ({ page }) => {
    // YouTube URL tab (default).
    await expect(page.locator("#youtube-url")).toBeVisible();
    await expect(page.getByText("Caption Style")).toBeVisible();
    await expect(page.getByText("Framing")).toBeVisible();
    await expect(page.getByText("Add subtitles")).toBeVisible();
    await expect(page.getByText("B-roll footage")).toBeVisible();
    await expect(page.getByRole("button", { name: /no b-roll/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /use b-roll/i })).toBeVisible();
    await expect(page.getByText("Clip cleanup")).toBeVisible();

    // At least the subtitles + cleanup switches are present.
    await expect(page.getByRole("switch").first()).toBeVisible();

    // Upload Video tab is present.
    await expect(page.getByRole("button", { name: /upload video/i })).toBeVisible();
  });

  test("submit validation: empty URL keeps Process Video disabled; invalid URL shows inline error", async ({
    page,
  }) => {
    const processButton = page.getByRole("button", { name: /process video/i });

    // Contract note: the form guards empty input by DISABLING the submit
    // button (there is no inline error message for empty input by design).
    await expect(processButton).toBeDisabled();

    // A non-YouTube URL passes the client-side gate but is rejected by the
    // backend — the error surfaces inline in the form.
    await page.locator("#youtube-url").fill("https://example.com/not-a-youtube-url");
    await expect(processButton).toBeEnabled();
    await processButton.click();
    await expect(
      page.getByText(/only youtube urls or upload:\/\/ references are supported/i),
    ).toBeVisible({ timeout: 20000 });
  });

  test("latest-generation task card shows title, status, and clip count", async ({
    page,
  }) => {
    const fixtures = readFixtures();
    test.skip(
      !fixtures.fixtureTaskAvailable,
      "no completed fixture task for this user (fresh suite user) — skipping task card assertion",
    );

    // The latest-generation banner links to the newest task.
    const banner = page.locator('a[href^="/tasks/"]').first();
    await expect(banner).toBeVisible();

    // Title, status label, and clips count are all observable on the card.
    await expect(banner.locator("p").first()).not.toBeEmpty();
    await expect(banner.getByText(/clip/i)).toBeVisible();
    await expect(banner.getByText(/completed|queued|processing|error|cancelled/i)).toBeVisible();
  });
});
