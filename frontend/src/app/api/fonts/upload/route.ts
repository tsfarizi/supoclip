import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { getBackendApiBaseUrl } from "@/server/backend-api";

export async function POST(request: Request) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const formData = await request.formData();
  const apiUrl = getBackendApiBaseUrl();
  const backendAuthHeaders = buildBackendAuthHeaders(session.user.id);

  let upstream = await fetch(`${apiUrl}/fonts/upload`, {
    method: "POST",
    headers: {
      ...backendAuthHeaders,
    },
    body: formData,
  });

  if (upstream.status === 404) {
    upstream = await fetch(`${apiUrl}/api/fonts/upload`, {
      method: "POST",
      headers: {
        ...backendAuthHeaders,
      },
      body: formData,
    });
  }

  const responseText = await upstream.text();
  return new NextResponse(responseText, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
    },
  });
}
