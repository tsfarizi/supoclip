/**
 * Public (unauthenticated) pages: landing, auth entry points, blog, SEO guide,
 * and the invalid share-token error state.
 *
 * These tests run WITHOUT storageState, i.e. in fresh unauthenticated
 * contexts. Note: the sign-in/sign-up card titles are <div> elements (shadcn
 * CardTitle), so assertions target visible text, not heading roles.
 */

import { expect, test } from "@playwright/test";

test.describe("public pages", () => {
  test("landing page shows brand, pricing (50/300), and footer", async ({ page }) => {
    await page.goto("/");

    // Hero: observable title and CTA.
    await expect(
      page.getByRole("heading", { name: /open-source ai video clipper/i }),
    ).toBeVisible();

    // Pricing section: the two paid tiers advertise 50 and 300 generations.
    await expect(page.getByText("50 generations per month")).toBeVisible();
    await expect(page.getByText("300 generations per month")).toBeVisible();

    // Footer navigation links (scoped to the footer to avoid the nav "Blog").
    const footer = page.getByLabel("Footer navigation");
    await expect(footer.getByRole("link", { name: "Privacy" })).toBeVisible();
    await expect(footer.getByRole("link", { name: "Terms" })).toBeVisible();
    await expect(footer.getByRole("link", { name: "Blog" })).toBeVisible();
  });

  test("/sign-in renders the sign-in form", async ({ page }) => {
    await page.goto("/sign-in");
    await expect(page.getByText("Sign in to your account")).toBeVisible();
    await expect(page.getByPlaceholder("Email")).toBeVisible();
    await expect(page.getByPlaceholder("Password")).toBeVisible();
    await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  });

  test("/sign-up renders the sign-up form", async ({ page }) => {
    await page.goto("/sign-up");
    await expect(page.getByText("Create a new account to get started")).toBeVisible();
    await expect(page.getByPlaceholder("Full Name")).toBeVisible();
    await expect(page.getByPlaceholder("Email")).toBeVisible();
    await expect(page.getByPlaceholder("Password")).toBeVisible();
    await expect(page.getByRole("button", { name: /sign up/i })).toBeVisible();
  });

  test("/blog lists guides and links to an article", async ({ page }) => {
    await page.goto("/blog");
    await expect(
      page.getByRole("heading", {
        name: /practical guides for turning long videos into better shorts/i,
      }),
    ).toBeVisible();
    // Featured article is a link to a blog post.
    await expect(page.getByText("Read article")).toBeVisible();
    await expect(page.locator('a[href^="/blog/"]').first()).toBeVisible();
  });

  test("/ai-video-clipper renders the SEO guide", async ({ page }) => {
    await page.goto("/ai-video-clipper");
    await expect(
      page.getByRole("heading", {
        name: /ai video clipper for turning long videos into better shorts/i,
      }),
    ).toBeVisible();
  });

  test("/share with an invalid token shows the not-found message", async ({ page }) => {
    await page.goto("/share/invalid-token-xyz");
    await expect(page.getByText("Shared result not found")).toBeVisible({ timeout: 15000 });
  });
});
