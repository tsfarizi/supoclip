/**
 * Task list and completed-task detail page: clips, scores, transcript,
 * Download/Edit actions, merge selection, Open Editor, share, project settings.
 *
 * Starts authenticated via the shared storageState (see global-setup).
 */

import { expect, test } from "@playwright/test";

import {
  findCompletedTaskHref,
  getStorageStatePath,
  notePlaceholderPoster404,
  readFixtures,
} from "./helpers";

test.use({ storageState: getStorageStatePath() });

test.describe("tasks", () => {
  test("/list renders the generations page with the fixture task", async ({ page }) => {
    const fixtures = readFixtures();
    await page.goto("/list");
    await expect(page.getByRole("heading", { name: /generations/i })).toBeVisible();

    if (fixtures.fixtureTaskAvailable) {
      // The fixture user may own more than one task with this title.
      await expect(page.getByText(fixtures.fixtureTaskTitle).first()).toBeVisible();
      await expect(page.getByText("Completed").first()).toBeVisible();
    } else {
      // Fresh user has no tasks — the empty state is the observable contract.
      await expect(page.getByText("No generations yet")).toBeVisible();
    }
  });

  test("completed task detail shows 4 clips with scores, transcript, and actions", async ({
    page,
  }) => {
    const fixtures = readFixtures();
    test.skip(
      !fixtures.fixtureTaskAvailable,
      "no completed fixture task for this user (fresh suite user) — skipping detail assertions",
    );

    notePlaceholderPoster404(page);

    const href = await findCompletedTaskHref(page);
    expect(href, "expected a completed task in /list").not.toBeNull();
    await page.goto(href!);

    // Task header.
    await expect(page.getByRole("heading", { name: fixtures.fixtureTaskTitle })).toBeVisible();
    await expect(page.getByText("4 clips generated")).toBeVisible();

    // 4 clip cards: merge selection, transcripts, and action buttons.
    // (Action counts are scoped to the clip cards because the header also has
    // an icon-only "Edit" button for renaming the task title.)
    const clipCards = page.locator("div.grid.gap-6 > div.overflow-hidden");
    await expect(clipCards).toHaveCount(4);
    await expect(page.getByText("Select for merge")).toHaveCount(4);
    await expect(page.getByText("Transcript")).toHaveCount(4);
    await expect(clipCards.getByRole("button", { name: /download/i })).toHaveCount(4);
    await expect(clipCards.getByRole("button", { name: /edit/i })).toHaveCount(4);

    // Scores: the relevance badge is rendered per clip (e.g. "92%").
    await expect(page.getByText(/^\d{1,3}%$/)).toHaveCount(4);
    // Fixture data currently stores virality_score = 0 for all clips, and the
    // app only renders the virality badge/breakdown when the score is > 0 —
    // so the virality UI is intentionally absent for this fixture. See README.

    // Detail-page controls.
    await expect(page.getByRole("link", { name: /open editor/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /copy share link/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /project settings/i })).toBeVisible();

    // Project Settings sheet opens on demand.
    await page.getByRole("button", { name: /project settings/i }).click();
    await expect(page.getByText("Apply to All Clips")).toBeVisible();
  });
});
