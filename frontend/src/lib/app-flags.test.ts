import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ENV_KEY = "NEXT_PUBLIC_LANDING_ONLY_MODE";

// isLandingOnlyModeEnabled is evaluated at module load, so the module must be
// re-imported after each env mutation to observe a fresh value.
async function loadLandingOnlyFlag(): Promise<boolean> {
  const mod = await import("./app-flags");
  return mod.isLandingOnlyModeEnabled;
}

describe("isLandingOnlyModeEnabled", () => {
  const originalEnv = process.env[ENV_KEY];

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env[ENV_KEY];
    } else {
      process.env[ENV_KEY] = originalEnv;
    }
  });

  it("is disabled when the env var is unset", async () => {
    delete process.env[ENV_KEY];

    expect(await loadLandingOnlyFlag()).toBe(false);
  });

  it("is enabled only for the exact value 'true'", async () => {
    process.env[ENV_KEY] = "true";

    expect(await loadLandingOnlyFlag()).toBe(true);
  });

  it("is disabled for the string 'false'", async () => {
    process.env[ENV_KEY] = "false";

    expect(await loadLandingOnlyFlag()).toBe(false);
  });

  it("is disabled for truthy-looking values other than 'true'", async () => {
    process.env[ENV_KEY] = "1";

    expect(await loadLandingOnlyFlag()).toBe(false);
  });

  it("is disabled for uppercase 'TRUE' (case-sensitive comparison)", async () => {
    process.env[ENV_KEY] = "TRUE";

    expect(await loadLandingOnlyFlag()).toBe(false);
  });

  it("is disabled for whitespace around 'true'", async () => {
    process.env[ENV_KEY] = " true ";

    expect(await loadLandingOnlyFlag()).toBe(false);
  });
});
