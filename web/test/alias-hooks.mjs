import { existsSync } from "node:fs"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const srcDir = path.resolve(import.meta.dirname, "../src")
const nextStub = pathToFileURL(path.resolve(import.meta.dirname, "next-server-stub.mjs")).href

function resolveAlias(specifier) {
  const rel = specifier.slice(2)
  const base = path.join(srcDir, rel)
  const candidates = [base, `${base}.ts`, `${base}.tsx`, `${base}.mjs`, path.join(base, "index.ts")]
  return candidates.find((candidate) => existsSync(candidate))
}

export async function resolve(specifier, context, nextResolve) {
  if (specifier === "next/server") {
    return { url: nextStub, shortCircuit: true }
  }
  if (specifier.startsWith("@/")) {
    const hit = resolveAlias(specifier)
    if (!hit) {
      throw new Error(`cannot resolve ${specifier}`)
    }
    return nextResolve(pathToFileURL(hit).href, context)
  }
  return nextResolve(specifier, context)
}
