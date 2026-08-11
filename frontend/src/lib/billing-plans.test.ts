import { describe, expect, it } from "vitest";
import {
  formatBillingPlanName,
  getPublicBillingPlans,
  isPaidBillingPlan,
  PAID_PLAN_IDS,
} from "./billing-plans";

describe("getPublicBillingPlans", () => {
  it("returns marketing generation limits 50/300 without any env override", () => {
    delete process.env.NEXT_PUBLIC_PRO_PRICE_MONTHLY;
    delete process.env.NEXT_PUBLIC_SCALE_PRICE_MONTHLY;

    const plans = getPublicBillingPlans();

    expect(plans.map((plan) => plan.id)).toEqual(["pro", "scale"]);
    const pro = plans.find((plan) => plan.id === "pro");
    const scale = plans.find((plan) => plan.id === "scale");
    expect(pro?.generationLimit).toBe(50);
    expect(scale?.generationLimit).toBe(300);
  });

  it("keeps limits fixed at 50/300 even when price env vars are set (A12)", () => {
    process.env.NEXT_PUBLIC_PRO_PRICE_MONTHLY = "9";
    process.env.NEXT_PUBLIC_SCALE_PRICE_MONTHLY = "49";

    const plans = getPublicBillingPlans();
    const pro = plans.find((plan) => plan.id === "pro");
    const scale = plans.find((plan) => plan.id === "scale");

    expect(pro?.generationLimit).toBe(50);
    expect(scale?.generationLimit).toBe(300);
  });

  it("defaults monthly price to 10/50 when env vars are absent", () => {
    delete process.env.NEXT_PUBLIC_PRO_PRICE_MONTHLY;
    delete process.env.NEXT_PUBLIC_SCALE_PRICE_MONTHLY;

    const plans = getPublicBillingPlans();

    expect(plans.find((plan) => plan.id === "pro")?.priceMonthly).toBe("10");
    expect(plans.find((plan) => plan.id === "scale")?.priceMonthly).toBe("50");
  });

  it("marks pro as highlighted and scale as not", () => {
    const plans = getPublicBillingPlans();

    expect(plans.find((plan) => plan.id === "pro")?.highlighted).toBe(true);
    expect(plans.find((plan) => plan.id === "scale")?.highlighted).toBe(false);
  });
});

describe("formatBillingPlanName", () => {
  it.each([
    ["pro", "Pro"],
    ["scale", "Scale"],
    ["self_host", "Self-Hosted"],
    [null, "Free"],
    [undefined, "Free"],
    ["free", "Free"],
    ["unknown_plan", "Free"],
  ])("formatBillingPlanName(%j) === %j", (plan, expected) => {
    expect(formatBillingPlanName(plan)).toBe(expected);
  });
});

describe("isPaidBillingPlan", () => {
  it.each([
    ["pro", true],
    ["scale", true],
    [null, false],
    [undefined, false],
    ["free", false],
    ["self_host", false],
  ])("isPaidBillingPlan(%j) === %j", (plan, expected) => {
    expect(isPaidBillingPlan(plan)).toBe(expected);
  });

  it("exposes paid plan ids consistent with the predicate", () => {
    for (const id of PAID_PLAN_IDS) {
      expect(isPaidBillingPlan(id)).toBe(true);
    }
  });
});
