/**
 * Shared helpers for the SupoClip E2E specs.
 *
 * All helpers operate on OBSERVABLE behavior (page text, URLs, roles) — they
 * never read component internals.
 */

import fs from "fs";
import path from "path";

import { expect, type Page } from "@playwright/test";

export interface Credentials {
  email: string;
  password: string;
  source: "existing-e2e-user" | "created-suite-user";
  fixtureTaskAvailable: boolean;
}

export interface Fixtures {
  fixtureTaskAvailable: boolean;
  fixtureTaskTitle: string;
  note: string;
}

export function getBaseUrl(): string {
  return process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3107";
}

/** Path of the pre-authenticated session written by global-setup. */
export function getStorageStatePath(): string {
  return path.join(process.cwd(), "storageState.json");
}

/** Empty storage state for tests that must start unauthenticated. */
export const EMPTY_STORAGE_STATE = { cookies: [], origins: [] };

function readJson<T>(filename: string): T {
  const filePath = path.join(process.cwd(), filename);
  return JSON.parse(fs.readFileSync(filePath, "utf8")) as T;
}

export function readCredentials(): Credentials {
  return readJson<Credentials>(".credentials.json");
}

export function readFixtures(): Fixtures {
  return readJson<Fixtures>(".fixtures.json");
}

/** Sign in through the UI and wait for the authenticated home to render. */
export async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/sign-in");
  await page.getByPlaceholder("Email").fill(email);
  await page.getByPlaceholder("Password").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(
    page.getByRole("heading", { name: /create new clip/i }),
  ).toBeVisible({ timeout: 20000 });
}

/**
 * Find the id of a COMPLETED task owned by the signed-in user from /list.
 * Returns the task href (e.g. "/tasks/<uuid>") or null when none exists.
 */
export async function findCompletedTaskHref(page: Page): Promise<string | null> {
  await page.goto("/list");
  await expect(page.getByRole("heading", { name: /generations/i })).toBeVisible();

  const links = page.locator('a[href^="/tasks/"]');
  // Task rows load after the header; wait for the first one before counting.
  try {
    await links.first().waitFor({ state: "attached", timeout: 15000 });
  } catch {
    return null;
  }

  const count = await links.count();
  for (let i = 0; i < count; i++) {
    const link = links.nth(i);
    const text = (await link.textContent()) || "";
    if (text.includes("Completed")) {
      return link.getAttribute("href");
    }
  }
  return null;
}

/**
 * Watch page console for the known /placeholder-video.jpg 404 and report it
 * in the test's stdout (it is a tracked, known issue — never a test failure).
 */
export function notePlaceholderPoster404(page: Page): void {
  page.on("console", (msg) => {
    const text = msg.text();
    if (msg.type() === "error" && text.includes("placeholder-video")) {
      console.log(
        `[known issue] /placeholder-video.jpg 404 observed in console: ${text.slice(0, 200)}`,
      );
    }
  });
}
