import { describe, expect, it } from "vitest";
import { normalizeVideoIdentity } from "./video-identity";

describe("normalizeVideoIdentity (R3 cross-language mirror)", () => {
  // Vectors copied VERBATIM from backend/tests/unit/test_task_service.py
  // test_normalize_video_identity_vectors (12 parametrize rows).
  const vectors: Array<[string | null, string]> = [
    ["https://youtu.be/AbC123", "youtube:AbC123"],
    ["https://www.youtube.com/watch?v=AbC123", "youtube:AbC123"],
    ["https://youtube.com/watch?v=AbC123", "youtube:AbC123"],
    ["https://www.youtube.com/watch?v=AbC123&t=30s", "youtube:AbC123"],
    ["https://youtu.be/AbC123?si=tracking_param", "youtube:AbC123"],
    ["  https://youtu.be/AbC123  ", "youtube:AbC123"],
    ["https://www.youtube.com/watch?v=AbC_12-xY", "youtube:AbC_12-xY"],
    ["upload://demo.mp4", "upload://demo.mp4"],
    ["https://example.com/video.mp4?x=1", "https://example.com/video.mp4?x=1"],
    // YouTube id shorter than 6 chars is not recognized -> verbatim
    ["https://youtu.be/short", "https://youtu.be/short"],
    ["", ""],
    [null, ""],
  ];

  it.each(vectors)("normalizeVideoIdentity(%j) === %j", (url, expected) => {
    expect(normalizeVideoIdentity(url as string)).toBe(expected);
  });
});
