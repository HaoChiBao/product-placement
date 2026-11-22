/** Files under DATA_DIR/jobs/<id>/ that GET /api/jobs/:id/file/:name may serve. */
const FIXED: Record<string, string> = {
  "original.mp4": "video/mp4",
  "result.mp4": "video/mp4",
  "contact_sheet.jpg": "image/jpeg",
}

/** Content type for an allowed job file name, else null. Research image candidates are candidate_1..3.png. */
export function jobFileType(name: string): string | null {
  if (Object.prototype.hasOwnProperty.call(FIXED, name)) return FIXED[name]
  if (/^candidate_[1-3]\.png$/.test(name)) return "image/png"
  return null
}
