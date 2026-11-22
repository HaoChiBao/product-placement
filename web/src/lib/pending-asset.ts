/** Hands the optional product image from onboarding to the research page (same tab, in memory). */
let pending: File | null = null

export function setPendingAsset(file: File | null) {
  pending = file
}

/** Read the pending file without claiming it (e.g. for a preview). */
export function peekPendingAsset(): File | null {
  return pending
}

export function takePendingAsset(): File | null {
  const f = pending
  pending = null
  return f
}
