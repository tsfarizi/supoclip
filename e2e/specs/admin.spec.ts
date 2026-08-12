/**
 * Admin gate for a NON-admin user: the page explains the role and must NOT
 * expose admin-only sections (runtime settings, dashboard metrics).
 *
 * Starts authenticated via the shared storageState (see global-setup).
 */

import { expect, test } from "@playwright/test";

import { getStorageStatePath } from "./helpers";

test.use({ storageState: getStorageStatePath() });

test.describe("admin gate", () => {
  test("non-admin user is blocked from the admin dashboard", async ({ page }) => {
    await page.goto("/admin");

    // The gate page identifies the problem.
    await expect(page.getByRole("heading", { name: /admin/i })).toBeVisible();
    await expect(
      page.getByText(/you are signed in, but your account is not an admin/i),
    ).toBeVisible();

    // Admin-only content must not be rendered.
    await expect(page.getByText("Runtime Settings")).toHaveCount(0);
    await expect(page.getByText("Admin Dashboard")).toHaveCount(0);
    await expect(page.getByText(/currently processing tasks/i)).toHaveCount(0);
  });
});
