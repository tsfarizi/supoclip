/**
 * Upload flow: selecting a file arms the form, submitting uploads the file
 * (backend stores it and returns an upload:// reference), a task is created
 * with that reference, and the UI redirects to the task page.
 *
 * The environment may or may not run a worker; the task is NOT expected to
 * reach "completed" here (no transcription provider is required for the flow
 * assertion). The test asserts the upload:// wiring + task page render, then
 * cleans up the task it created so the fixture user's data is unchanged.
 *
 * Starts authenticated via the shared storageState (see global-setup).
 */

import { expect, test } from "@playwright/test";

import { getStorageStatePath } from "./helpers";

test.use({ storageState: getStorageStatePath() });

test.describe("upload", () => {
  test("upload a dummy video file and create a task", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /create new clip/i }),
    ).toBeVisible();

    // Switch to the Upload Video tab and attach the dummy file.
    await page.getByRole("button", { name: /upload video/i }).click();
    await page.locator("#video-upload").setInputFiles("fixtures/dummy.mp4");

    // The dropzone reflects the selected file.
    await expect(page.getByText("dummy.mp4")).toBeVisible();
    const processButton = page.getByRole("button", { name: /process video/i });
    await expect(processButton).toBeEnabled();

    // Capture the task-create request so we can assert the uploaded file is
    // wired in as an upload:// source reference.
    const createRequestPromise = page.waitForRequest(
      (req) => req.method() === "POST" && req.url().includes("/api/tasks/create"),
      { timeout: 20000 },
    );
    await processButton.click();

    const createRequest = await createRequestPromise;
    const createPayload = createRequest.postDataJSON() as {
      source?: { url?: string };
    };
    expect(createPayload.source?.url).toMatch(/^upload:\/\//);

    // The app redirects to the new task page.
    await page.waitForURL(/\/tasks\/[0-9a-f-]+/, { timeout: 20000 });
    const taskId = page.url().match(/\/tasks\/([0-9a-f-]+)/)?.[1];
    expect(taskId).toBeTruthy();

    // The task page renders the uploaded source (title derived by the backend).
    await expect(
      page.getByRole("heading", { name: /uploaded video/i }),
    ).toBeVisible({ timeout: 20000 });

    // Leave the task page first: the queued/processing page holds an SSE
    // connection, and deleting the task while it is open has been observed to
    // leave the backend in a degraded state. Navigating away closes it.
    await page.goto("/");

    // Cleanup: delete the task created by this test so the fixture user's
    // task list is unchanged after the run.
    const deleteResponse = await page.request.delete(`/api/tasks/${taskId}`);
    expect(deleteResponse.ok()).toBeTruthy();

    const fetchDeleted = await page.request.get(`/api/tasks/${taskId}`);
    expect(fetchDeleted.status()).toBe(404);
  });
});
