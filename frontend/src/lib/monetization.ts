export function isMonetizationEnabled(value: string | undefined): boolean {
  const normalized = value?.trim().toLowerCase();
  // Hosted default (undefined/empty) => monetization enabled; self-hosted ("true"/"1"/"yes") => disabled
  return normalized !== "true" && normalized !== "1" && normalized !== "yes";
}

export const monetizationEnabled = isMonetizationEnabled(process.env.NEXT_PUBLIC_SELF_HOST);
