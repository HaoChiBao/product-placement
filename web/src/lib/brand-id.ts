/** Safe brand / asset path segments under DATA_DIR/brands. */

const BRAND_ID_RE = /^[a-zA-Z0-9_-]{1,64}$/

export function isSafeBrandId(brandId: string): boolean {
  return BRAND_ID_RE.test(brandId)
}

export function safeAssetName(name: string): string | null {
  const base = name.split(/[/\\]/).pop() || ""
  if (!base || base === "." || base === "..") return null
  if (base.includes("\0")) return null
  return base
}
