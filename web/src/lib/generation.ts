/** Server-side gate for paid vs mock vs hold-when-key-missing. */

export type SwapMethod = "wan" | "higgsfield"

export type GenerationPlan = {
  testMode: boolean
  canSpawn: boolean
  mockEnv: boolean
  keysConfigured: boolean
  method: SwapMethod
  holdError?: string
}

const HIGGSFIELD_KEY_MISSING =
  "Generation queued: Higgsfield API key missing (set HF_KEY or HF_API_KEY_ID + HF_API_KEY_SECRET)"

const WAN_KEY_MISSING =
  "Generation queued: OpenRouter API key missing (set OPENROUTER_API_KEY)"

const S3_MISSING =
  "Generation queued: S3 key missing (set AWS_S3_BUCKET_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)"

const FRAME_METHODS = new Set(["higgsfield", "frame", "seedance", "segment"])

export function resolveSwapMethod(raw?: string | null): SwapMethod {
  const picked = (raw ?? "").trim()
  const value = (picked || process.env.SWAP_METHOD || "wan").trim().toLowerCase()
  return FRAME_METHODS.has(value) ? "higgsfield" : "wan"
}

function mockEnvOn(): boolean {
  return ["1", "true", "yes", "on"].includes(
    (process.env.MOCK_HIGGSFIELD || "").trim().toLowerCase()
  )
}

function higgsfieldKeysConfigured(): boolean {
  if ((process.env.HF_KEY || "").trim()) return true
  const id = (process.env.HF_API_KEY_ID || "").trim()
  const secret = (process.env.HF_API_KEY_SECRET || "").trim()
  return Boolean(id && secret)
}

function openrouterKeyConfigured(): boolean {
  return Boolean((process.env.OPENROUTER_API_KEY || "").trim())
}

function s3Configured(): boolean {
  return Boolean(
    (process.env.AWS_S3_BUCKET_NAME || "").trim() &&
      (process.env.AWS_ACCESS_KEY_ID || "").trim() &&
      (process.env.AWS_SECRET_ACCESS_KEY || "").trim()
  )
}

/** Resolve whether to spawn a real/mock swap or hold the placement queued. */
export function planGeneration(opts?: {
  testMode?: boolean
  method?: string | null
}): GenerationPlan {
  const method = resolveSwapMethod(opts?.method)
  const mock = mockEnvOn()
  const explicitTest = opts?.testMode === true
  const explicitPaid = opts?.testMode === false
  const hfKeys = higgsfieldKeysConfigured()
  const orKey = openrouterKeyConfigured()
  const s3 = s3Configured()
  const keys = method === "higgsfield" ? hfKeys : orKey && s3

  if (explicitTest || (mock && !explicitPaid)) {
    return { testMode: true, canSpawn: true, mockEnv: mock, keysConfigured: keys, method }
  }
  if (method === "higgsfield") {
    if (hfKeys) {
      return { testMode: false, canSpawn: true, mockEnv: mock, keysConfigured: true, method }
    }
    return {
      testMode: false,
      canSpawn: false,
      mockEnv: mock,
      keysConfigured: false,
      method,
      holdError: HIGGSFIELD_KEY_MISSING,
    }
  }
  if (!orKey) {
    return {
      testMode: false,
      canSpawn: false,
      mockEnv: mock,
      keysConfigured: false,
      method,
      holdError: WAN_KEY_MISSING,
    }
  }
  if (!s3) {
    return {
      testMode: false,
      canSpawn: false,
      mockEnv: mock,
      keysConfigured: false,
      method,
      holdError: S3_MISSING,
    }
  }
  return { testMode: false, canSpawn: true, mockEnv: mock, keysConfigured: true, method }
}

export function generationHealth() {
  const plan = planGeneration()
  return {
    ready: plan.canSpawn && !plan.testMode,
    mock: plan.mockEnv || plan.testMode,
    keysConfigured: plan.keysConfigured,
    method: plan.method,
    reason: plan.holdError || (plan.testMode ? "mock generation enabled" : null),
  }
}
