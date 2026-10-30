// Save text the browser already has (or just fetched) as a file. Kept tiny and
// DOM-only: the pure naming rules live in trend.ts and are unit-tested.

export function downloadTextFile(
  filename: string,
  text: string,
  mimeType: string,
): void {
  const blob = new Blob([text], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
