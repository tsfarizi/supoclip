import "server-only";

import { headers } from "next/headers";

import { auth } from "@/server/auth";

export async function getServerSession() {
  return auth.api.getSession({ headers: await headers() });
}
