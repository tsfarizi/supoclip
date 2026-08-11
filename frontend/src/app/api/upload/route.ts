import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { createProxyResponse, fetchBackend } from "@/server/backend-api";

export async function POST(request: Request) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const formData = await request.formData();
  const upstream = await fetchBackend("/upload", {
    method: "POST",
    userId: session.user.id,
    body: formData,
  });

  return createProxyResponse(upstream);
}
