/**
 * Public share flow: create a share link from a completed task, verify the
 * public page (unauthenticated), then disable sharing and verify the link
 * returns the not-found state.
 *
 * The task page starts authenticated via the shared storageState. Public
 * pages open in FRESH contexts (no storageState → no auth).
 *
 * Each run starts from a share-disabled state (resets any leftover from a
 * previous crashed run), so the flow is deterministic and repeatable.
 */

import { expect, test, type Browser, type Page } from "@playwright/test";

import {
  findCompletedTaskHref,
  getBaseUrl,
  getStorageStatePath,
  readFixtures,
} from "./helpers";

test.use({ storageState: getStorageStatePath() });

async function openShareInFreshContext(browser: Browser, url: string): Promise<Page> {
  const context = await browser.newContext();
  const sharePage = await context.newPage();
  await sharePage.goto(url);
  return sharePage;
}

test.describe("share", () => {
  test("copy share link → public page → disable share → link 404s", async ({
    page,
    browser,
  }) => {
    const fixtures = readFixtures();
    test.skip(
      !fixtures.fixtureTaskAvailable,
      "no completed fixture task for this user (fresh suite user) — skipping share flow",
    );

    const href = await findCompletedTaskHref(page);
    expect(href, "expected a completed task in /list").not.toBeNull();
    await page.goto(href!);

    // Wait for the completed task page to load clips.
    await expect(page.getByText(fixtures.fixtureTaskTitle)).toBeVisible();

    // Idempotency: if a previous run left sharing enabled, reset it first.
    const disableButton = page.getByRole("button", { name: /disable share link/i });
    if (await disableButton.isVisible().catch(() => false)) {
      const disableResponse = page.waitForResponse(
        (res) =>
          res.request().method() === "DELETE" && /\/api\/tasks\/[^/]+\/share$/.test(res.url()),
        { timeout: 15000 },
      );
      await disableButton.click();
      const response = await disableResponse;
      expect(response.ok()).toBeTruthy();
    }

    // 1) Create the share link and capture its path from the API response.
    const shareResponsePromise = page.waitForResponse(
      (res) =>
        res.request().method() === "POST" && /\/api\/tasks\/[^/]+\/share$/.test(res.url()),
      { timeout: 15000 },
    );
    await page.getByRole("button", { name: /copy share link/i }).click();
    const shareResponse = await shareResponsePromise;
    expect(shareResponse.ok()).toBeTruthy();
    const sharePayload = (await shareResponse.json()) as { share_path: string };
    expect(sharePayload.share_path).toMatch(/^\/share\/[A-Za-z0-9_-]+$/);
    const shareUrl = new URL(sharePayload.share_path, getBaseUrl()).toString();

    // Button confirms the copy.
    await expect(page.getByRole("button", { name: /link copied/i })).toBeVisible();
    // Disable control becomes available once sharing is enabled.
    await expect(page.getByRole("button", { name: /disable share link/i })).toBeVisible();

    // 2) The public page (fresh context, NO auth) renders the shared result.
    const publicPage = await openShareInFreshContext(browser, shareUrl);
    await expect(publicPage.getByText("Shared generation")).toBeVisible({ timeout: 15000 });
    await expect(publicPage.getByRole("heading", { name: fixtures.fixtureTaskTitle })).toBeVisible();
    await expect(publicPage.getByRole("link", { name: /download clip/i })).toHaveCount(4);
    await expect(publicPage.locator("video[aria-label='Video player']")).toHaveCount(4);
    await publicPage.context().close();

    // 3) Disable sharing from the task page.
    const revokeResponsePromise = page.waitForResponse(
      (res) =>
        res.request().method() === "DELETE" && /\/api\/tasks\/[^/]+\/share$/.test(res.url()),
      { timeout: 15000 },
    );
    await page.getByRole("button", { name: /disable share link/i }).click();
    const revokeResponse = await revokeResponsePromise;
    expect(revokeResponse.ok()).toBeTruthy();

    // 4) The public URL is now gone (404 → not-found page).
    const revokedPage = await openShareInFreshContext(browser, shareUrl);
    await expect(revokedPage.getByText("Shared result not found")).toBeVisible({
      timeout: 15000,
    });
    await revokedPage.context().close();
  });
});
