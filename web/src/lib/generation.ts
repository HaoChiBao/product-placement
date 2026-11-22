/** Server-side gate for paid vs mock vs hold-when-key-missing. */

export type GenerationPlan = {
  testMode: boolean
  canSpawn: boolean
  mockEnv: boolean
  keysConfigured: boolean
  holdError?: string
}

const KEY_MISSING =
  "Generation queued: Higgsfield API key missing (set HF_KEY or HF_API_KEY_ID + HF_API_KEY_SECRET)"

function mockEnvOn(): boolean {
  return ["1", "true", "yes", "on"].includes(
    (process.env.MOCK_HIGGSFIELD || "").trim().toLowerCase()
  )
}

function keysConfigured(): boolean {
  if ((process.env.HF_KEY || "").trim()) return true
  const id = (process.env.HF_API_KEY_ID || "").trim()
  const secret = (process.env.HF_API_KEY_SECRET || "").trim()
  return Boolean(id && secret)
}

/** Resolve whether to spawn a real/mock swap or hold the placement queued. */
export function planGeneration(opts?: { testMode?: boolean }): GenerationPlan {
  const mock = mockEnvOn()
  const keys = keysConfigured()
  const explicitTest = opts?.testMode === true
  const explicitPaid = opts?.testMode === false

  if (explicitTest || (mock && !explicitPaid)) {
    return { testMode: true, canSpawn: true, mockEnv: mock, keysConfigured: keys }
  }
  if (keys) {
    return { testMode: false, canSpawn: true, mockEnv: mock, keysConfigured: keys }
  }
  return {
    testMode: false,
    canSpawn: false,
    mockEnv: mock,
    keysConfigured: keys,
    holdError: KEY_MISSING,
  }
}

export function generationHealth() {
  const plan = planGeneration()
  return {
    ready: plan.canSpawn && !plan.testMode,
    mock: plan.mockEnv || plan.testMode,
    keysConfigured: plan.keysConfigured,
    reason: plan.holdError || (plan.testMode ? "mock generation enabled" : null),
  }
}
