// Resolved by the webpack alias in next.config.ts (local file if present, else the example).
declare module "@mock/research" {
  export const MOCK_RESEARCH: import("@/lib/research").MockResearch
}
