/** Hands the optional product image from onboarding to the research page (same tab, in memory). */
let pending: File | null = null

export function setPendingAsset(file: File | null) {
  pending = file
}

export function takePendingAsset(): File | null {
  const f = pending
  pending = null
  return f
}
