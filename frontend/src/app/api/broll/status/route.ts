import { NextResponse } from "next/server";

import { getBackendApiBaseUrl } from "@/server/backend-api";

// Proxy for the backend's public B-roll configuration status endpoint.
// The frontend uses this to tell users whether stock footage is available.
export async function GET() {
  const apiUrl = getBackendApiBaseUrl();
  const upstream = await fetch(`${apiUrl}/broll/status`, {
    method: "GET",
    cache: "no-store",
  });

  const responseText = await upstream.text();
  return new NextResponse(responseText, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
    },
  });
}
