import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { createProxyResponse, fetchBackend } from "@/server/backend-api";

export async function POST(request: Request) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  if (!request.body) {
    return NextResponse.json({ error: "Empty body" }, { status: 400 });
  }

  const upstream = await fetchBackend("/upload", {
    method: "POST",
    userId: session.user.id,
    headers: {
      "content-type": request.headers.get("content-type") || "multipart/form-data",
    },
    body: request.body,
    // Node fetch mewajibkan duplex untuk body bertipe stream; tipe RequestInit
    // TS tidak memuat `duplex`, gunakan cast minimal bila diperlukan.
    duplex: "half",
  } as RequestInit);

  return createProxyResponse(upstream);
}
