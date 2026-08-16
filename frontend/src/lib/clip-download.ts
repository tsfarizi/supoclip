export function buildClipDownloadFilename(
  title: string | null | undefined,
  clipOrder: number,
  suffix?: string,
): string {
  const fallbackTitle = `Clip ${clipOrder}`;
  const sourceTitle = typeof title === "string" && title.trim() ? title : fallbackTitle;
  let safeTitle = sourceTitle
    .replace(/\s+/g, " ")
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, "")
    .replace(/[<>:"/\\|?*]/g, "")
    .replace(/\.{2,}/g, ".")
    .trim()
    .replace(/[. ]+$/g, "");

  if (!safeTitle) {
    safeTitle = fallbackTitle;
  }

  const safeSuffix = (suffix ?? "")
    .replace(/\s+/g, " ")
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, "")
    .replace(/[<>:"/\\|?*]/g, "")
    .replace(/\.{2,}/g, ".")
    .replace(/[. ]+$/g, "");

  return `${safeTitle}${safeSuffix}.mp4`;
}
