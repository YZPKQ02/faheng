export function normalizeConsultationContent(content: string) {
  let normalized = content;
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
  return normalized;
}
