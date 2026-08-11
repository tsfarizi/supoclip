import { describe, expect, it } from "vitest";
import { buildSupportError, formatSupportMessage, parseApiError } from "./api-error";

function jsonResponse(
  body: unknown,
  headers: Record<string, string> = {}
): Response {
  return new Response(JSON.stringify(body), {
    status: 400,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("parseApiError", () => {
  it("uses payload.message when present", async () => {
    const info = await parseApiError(
      jsonResponse({ message: "Payment failed" }),
      "fallback"
    );

    expect(info.message).toBe("Payment failed");
    expect(info.traceId).toBeNull();
  });

  it("falls back to payload.error when message is missing", async () => {
    const info = await parseApiError(
      jsonResponse({ error: "Unauthorized" }),
      "fallback"
    );

    expect(info.message).toBe("Unauthorized");
  });

  it("falls back to string detail when message/error are missing", async () => {
    const info = await parseApiError(
      jsonResponse({ detail: "Invalid input" }),
      "fallback"
    );

    expect(info.message).toBe("Invalid input");
  });

  it("falls back to detail.message for object detail", async () => {
    const info = await parseApiError(
      jsonResponse({ detail: { message: "Nested message" } }),
      "fallback"
    );

    expect(info.message).toBe("Nested message");
  });

  it("prefers message over error over string detail over nested detail", async () => {
    const info = await parseApiError(
      jsonResponse({
        message: "top message",
        error: "top error",
        detail: "top detail",
      }),
      "fallback"
    );

    expect(info.message).toBe("top message");
  });

  it("uses fallbackMessage when the payload has no usable message", async () => {
    const info = await parseApiError(jsonResponse({}), "fallback text");

    expect(info.message).toBe("fallback text");
  });

  it("uses fallbackMessage for non-JSON responses", async () => {
    const response = new Response("Internal Server Error", {
      status: 500,
      headers: { "Content-Type": "text/plain" },
    });

    const info = await parseApiError(response, "fallback text");

    expect(info.message).toBe("fallback text");
    expect(info.traceId).toBeNull();
  });

  it("ignores whitespace-only message and uses the next source", async () => {
    const info = await parseApiError(
      jsonResponse({ message: "   ", error: "real error" }),
      "fallback"
    );

    expect(info.message).toBe("real error");
  });

  it("reads trace id from the x-trace-id header", async () => {
    const info = await parseApiError(
      jsonResponse({ message: "boom" }, { "x-trace-id": "trace-123" }),
      "fallback"
    );

    expect(info.traceId).toBe("trace-123");
  });

  it("reads trace id from body trace_id when no header is present", async () => {
    const info = await parseApiError(
      jsonResponse({ message: "boom", trace_id: "trace-body" }),
      "fallback"
    );

    expect(info.traceId).toBe("trace-body");
  });

  it("prefers the header trace id over body trace_id", async () => {
    const info = await parseApiError(
      jsonResponse(
        { message: "boom", trace_id: "trace-body" },
        { "x-trace-id": "trace-header" }
      ),
      "fallback"
    );

    expect(info.traceId).toBe("trace-header");
  });

  it("returns null trace id when both header and body trace are blank", async () => {
    const info = await parseApiError(
      jsonResponse({ message: "boom", trace_id: "   " }),
      "fallback"
    );

    expect(info.traceId).toBeNull();
  });
});

describe("formatSupportMessage", () => {
  it("returns the message verbatim when there is no trace id", () => {
    expect(formatSupportMessage({ message: "Payment failed", traceId: null })).toBe(
      "Payment failed"
    );
    expect(formatSupportMessage({ message: "Payment failed", traceId: "" })).toBe(
      "Payment failed"
    );
  });

  it("appends the trace id in parentheses when present", () => {
    expect(
      formatSupportMessage({ message: "Payment failed", traceId: "trace-123" })
    ).toBe("Payment failed (Trace ID: trace-123)");
  });
});

describe("buildSupportError", () => {
  it("formats the parsed message with trace id from the header", async () => {
    const message = await buildSupportError(
      jsonResponse({ message: "boom" }, { "x-trace-id": "trace-123" }),
      "fallback"
    );

    expect(message).toBe("boom (Trace ID: trace-123)");
  });

  it("returns the parsed message verbatim without a trace id", async () => {
    const message = await buildSupportError(
      jsonResponse({ detail: "Invalid input" }),
      "fallback"
    );

    expect(message).toBe("Invalid input");
  });

  it("falls back for non-JSON responses", async () => {
    const response = new Response("Internal Server Error", {
      status: 500,
      headers: { "Content-Type": "text/plain" },
    });

    const message = await buildSupportError(response, "fallback text");

    expect(message).toBe("fallback text");
  });
});
