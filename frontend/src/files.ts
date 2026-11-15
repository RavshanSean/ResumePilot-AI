export function fileError(file: Pick<File, 'name' | 'size'>): string | null {
  if (!/\.(pdf|docx)$/i.test(file.name)) return `${file.name}: only PDF and DOCX are supported.`;
  if (!file.size || file.size > 10 * 1024 * 1024) return `${file.name}: choose a nonempty file under 10 MB.`;
  return null;
}
