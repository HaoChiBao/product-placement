import { createHash, timingSafeEqual } from "crypto"

export type AdminImportGate =
  | { ok: true }
  | { ok: false; status: 404 | 401 }

/** 404 when ADMIN_IMPORT_TOKEN is unset so the route stays hidden. */
export function adminImportGate(
  header: string | null,
  envToken: string | undefined = process.env.ADMIN_IMPORT_TOKEN
): AdminImportGate {
  if (envToken == null || envToken.trim() === "") {
    return { ok: false, status: 404 }
  }
  const got = createHash("sha256").update(header ?? "", "utf8").digest()
  const expected = createHash("sha256").update(envToken, "utf8").digest()
  if (!timingSafeEqual(got, expected)) {
    return { ok: false, status: 401 }
  }
  return { ok: true }
}

export function adminImportErrorStatus(message: string): number {
  const lower = message.toLowerCase()
  if (lower.includes("not found")) return 404
  if (
    lower.includes("required") ||
    lower.includes("expected") ||
    lower.includes("invalid") ||
    lower.includes("greater") ||
    lower.includes("must be")
  ) {
    return 400
  }
  return 500
}
