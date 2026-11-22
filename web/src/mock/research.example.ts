/**
 * Example mock for brand research. Copy to `research.local.ts` (gitignored)
 * to use your own data locally; the app picks the local file when it exists.
 */
import type { MockResearch } from "@/lib/research"

const host = (site: string) => site.replace(/^https?:\/\//, "").replace(/\/$/, "") || "example.com"

export const MOCK_RESEARCH: MockResearch = {
  defaults: {
    business: "",
    website: "",
    product: "",
    note: "",
  },
  steps: (i) => [
    { label: "Searching the web", detail: `“${i.business}” · “${i.product}”` },
    { label: "Fetching your website", detail: `${host(i.website)} · 6 pages` },
    { label: "Reading product page", detail: "price, size, packaging" },
    { label: "Scanning socials and press", detail: "Instagram, TikTok, articles" },
    { label: "Building brand profile", detail: "tone, audience, visual identity" },
    { label: "Saving brand profile", detail: "POST /api/catalog/brands" },
  ],
  sources: (i) => [
    { kind: "search", url: `google · “${i.business}”`, ms: 800 },
    { kind: "fetch", url: host(i.website), ms: 1100 },
    { kind: "fetch", url: `${host(i.website)}/about`, ms: 600 },
    { kind: "fetch", url: `${host(i.website)}/products`, ms: 900 },
    { kind: "fetch", url: "instagram.com", ms: 1400 },
  ],
  profile: (i) => ({
    business: i.business,
    website: host(i.website),
    product: i.product,
    productFacts: "Facts from your product page appear here",
    location: "",
    category: "Consumer goods",
    tone: "Friendly, everyday",
    audience: "18–34",
    colors: ["#1f2a24", "#e9ece9", "#13a452"],
    goodScenes: ["tables", "desks", "kitchens"],
    avoidScenes: ["beds"],
    prefer: ["cups", "bottles", "tables"],
    avoid: ["beds"],
  }),
}
