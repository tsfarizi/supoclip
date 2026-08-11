// Mirrors backend normalize_video_identity (src/services/task_service.py):
// YouTube links collapse to the video id, everything else is compared
// verbatim. Used for duplicate in-flight submission detection.
export const normalizeVideoIdentity = (value: string): string => {
  const v = (value || "").trim();
  if (!v) return "";
  const match = v.match(
    /(?:youtu\.be\/|youtube\.com\/watch\?v=)([A-Za-z0-9_-]{6,})/
  );
  if (match) return `youtube:${match[1]}`;
  return v;
};
