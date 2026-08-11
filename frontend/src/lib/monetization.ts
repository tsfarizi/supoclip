export function isMonetizationEnabled(value: string | undefined): boolean {
  return value?.trim().toLowerCase() === "false";
}

export const monetizationEnabled = isMonetizationEnabled(process.env.NEXT_PUBLIC_SELF_HOST);
