import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  const session = await getServerSession();
  const { path } = await context.params;
  const incomingUrl = new URL(request.url);
  const targetPath = `/media/${path.join("/")}${incomingUrl.search}`;

  const upstream = await fetchBackend(targetPath, {
    method: "GET",
    ...(session?.user?.id ? { userId: session.user.id } : {}),
    extraHeaders: {
      ...(request.headers.get("accept")
        ? { Accept: request.headers.get("accept") as string }
        : {}),
      ...(request.headers.get("range")
        ? { Range: request.headers.get("range") as string }
        : {}),
    },
    cache: "no-store",
  });

  return createProxyResponse(upstream);
}
