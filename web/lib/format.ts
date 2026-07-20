export function normalizeConsultationContent(content: string) {
  let normalized = content
    .replace(/\*\*(\d{1,2}[.、．])\*\*/g, "$1")
    .replace(/[\u200B-\u200D\uFEFF]/g, "");
  let previous = "";
  while (normalized !== previous) {
    previous = normalized;
    normalized = normalized.replace(
      /^([ \t]*)(\d{1,2})[.、．][ \t]+(\d{1,2})[.、．][ \t]*/gm,
      (match, indent: string, outer: string, inner: string) => (
        outer === inner ? `${indent}${outer}. ` : match
      ),
    );
  }
  return normalized
    .replace(/([：:。；;！？!?])\s*(?=\d{1,2}[.、．](?:\s|[\u4E00-\u9FFF]))/g, "$1\n")
    .replace(/([^\n])\s+(?=\d{1,2}[.、．](?:\s|[\u4E00-\u9FFF]))/g, "$1\n")
    .replace(/([^\n])\s+(?=[•·-]\s+)/g, "$1\n");
}
