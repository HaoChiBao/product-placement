"use client"

import { AnimatePresence, motion } from "motion/react"
import {
  BookOpen,
  Globe,
  Search,
  Clapperboard,
  Coffee,
  CookingPot,
  Laptop,
  Package,
  Sunrise,
  type LucideIcon,
} from "lucide-react"

import type { BrandProfile, ResearchSource } from "@/lib/research"
import { cn } from "@/lib/utils"

const SCENE_ICONS: [RegExp, LucideIcon][] = [
  [/desk|office|laptop|work/i, Laptop],
  [/caf|coffee|cup|mug/i, Coffee],
  [/kitchen|cook|food/i, CookingPot],
  [/morning|routine|sunrise/i, Sunrise],
  [/study|book|read|school/i, BookOpen],
]

const sceneIcon = (scene: string) => SCENE_ICONS.find(([re]) => re.test(scene))?.[1] ?? Clapperboard

type Pin = { key: string; tilt: number; node: React.ReactNode; className?: string }

/** Polaroid-style scene card. */
function Polaroid({ scene }: { scene: string }) {
  const Icon = sceneIcon(scene)
  return (
    <div className="w-[132px] rounded-[4px] bg-[#f4f1ea] p-2 pb-3 shadow-[0_10px_24px_rgba(0,0,0,0.5)]">
      <div className="grid h-[96px] place-items-center rounded-[2px] bg-[#1c1c1c]">
        <Icon className="size-8 text-[#e50914]" strokeWidth={1.5} aria-hidden />
      </div>
      <p className="mt-2 text-center font-display text-[13px] font-medium capitalize text-[#1c1c1c]">{scene}</p>
    </div>
  )
}

/** Handwritten-feel note pinned with a strip of tape. */
function Note({ label, children, tone = "paper" }: { label: string; children: React.ReactNode; tone?: "paper" | "red" }) {
  return (
    <div
      className={cn(
        "relative w-[190px] rounded-[3px] px-4 pb-4 pt-5 shadow-[0_10px_24px_rgba(0,0,0,0.5)]",
        tone === "red" ? "bg-[#2a1012] text-[#ffd9db]" : "bg-[#fff6d6] text-[#3b2f12]"
      )}
    >
      <span className="absolute -top-2 left-1/2 h-4 w-14 -translate-x-1/2 rotate-[-3deg] bg-white/35" aria-hidden />
      <p className={cn("text-[10.5px] font-semibold uppercase tracking-[0.12em]", tone === "red" ? "text-[#ff6b72]" : "text-[#8a6d1f]")}>
        {label}
      </p>
      <div className="mt-1.5 text-[14px] leading-snug">{children}</div>
    </div>
  )
}

/** Torn-out web clipping for each page the research agent reads. */
function Clipping({ source }: { source: ResearchSource }) {
  const Icon = source.kind === "search" ? Search : Globe
  return (
    <div className="w-[150px] rounded-[2px] bg-[#e9e6df] px-3 py-2.5 shadow-[0_8px_18px_rgba(0,0,0,0.45)] [clip-path:polygon(0_4%,8%_0,20%_3%,35%_0,50%_4%,64%_0,80%_3%,92%_0,100%_4%,100%_100%,0_100%)]">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#7a7466]">
        <Icon className="size-3" aria-hidden />
        {source.kind === "search" ? "Search" : "Read"}
      </div>
      <p className="mt-1 line-clamp-2 break-all text-[12px] leading-snug text-[#26231d]">{source.url}</p>
    </div>
  )
}

/**
 * Brand research as a moodboard: the product goes up first, a clipping lands for
 * each page the agent reads, then the finished profile (palette, tone, scenes,
 * audience, no-go scenes) gets pinned once it comes back.
 */
export function BrandMoodboard({
  product,
  productImage,
  sources,
  profile,
}: {
  product: string
  productImage?: string | null
  sources: ResearchSource[]
  profile: BrandProfile | null
}) {
  const pins: Pin[] = [
    {
      key: "product",
      tilt: -3,
      node: (
        <div className="w-[200px] rounded-[4px] bg-[#f4f1ea] p-2.5 pb-3 shadow-[0_12px_28px_rgba(0,0,0,0.55)]">
          <div className="grid h-[150px] place-items-center rounded-[2px] bg-[#1c1c1c]">
            {productImage ? (
              <img src={productImage} alt="" className="max-h-[130px] object-contain" />
            ) : (
              <Package className="size-10 text-[#e50914]" strokeWidth={1.5} aria-hidden />
            )}
          </div>
          <p className="mt-2 font-display text-[14px] font-bold leading-tight text-[#1c1c1c]">
            {profile?.product || product}
          </p>
          {profile?.productFacts ? (
            <p className="mt-0.5 text-[11.5px] leading-snug text-[#5b5546]">{profile.productFacts}</p>
          ) : null}
        </div>
      ),
    },
    ...sources.slice(0, 5).map((source, i) => ({
      key: `src-${source.url}`,
      tilt: [2, -3, 4, -2, 3][i] ?? 0,
      node: <Clipping source={source} />,
    })),
  ]

  if (profile) {
    pins.push(
      {
        key: "colors",
        tilt: 2,
        node: (
          <div className="rounded-[4px] bg-[#f4f1ea] p-3 shadow-[0_10px_24px_rgba(0,0,0,0.5)]">
            <p className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-[#8a7f66]">Palette</p>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {profile.colors.slice(0, 4).map((c) => (
                <div key={c} className="flex flex-col items-center gap-1">
                  <span className="size-11 rounded-[3px] ring-1 ring-black/10" style={{ background: c }} />
                  <span className="font-mono text-[10px] uppercase text-[#5b5546]">{c}</span>
                </div>
              ))}
            </div>
          </div>
        ),
      },
      { key: "tone", tilt: -2, node: <Note label="Tone">{profile.tone}</Note> },
      ...profile.goodScenes.slice(0, 5).map((scene, i) => ({
        key: `scene-${scene}`,
        tilt: [4, -5, 3, -2, 5][i] ?? 0,
        node: <Polaroid scene={scene} />,
      })),
      { key: "audience", tilt: 3, node: <Note label="Audience">{profile.audience}</Note> },
      {
        key: "avoid",
        tilt: -4,
        node: (
          <Note label="Keep out of" tone="red">
            <span className="flex flex-wrap gap-x-2 gap-y-0.5">
              {profile.avoidScenes.map((s) => (
                <span key={s} className="line-through decoration-[#e50914] decoration-2">
                  {s}
                </span>
              ))}
            </span>
          </Note>
        ),
      }
    )
  }

  const firstProfilePin = 1 + Math.min(5, sources.length)

  return (
    <div className="relative overflow-hidden rounded-lg border border-border bg-[#181818] px-6 py-8">
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.07] [background-image:radial-gradient(circle_at_1px_1px,#fff_1px,transparent_0)] [background-size:14px_14px]"
        aria-hidden
      />
      <div className="relative flex min-h-[420px] flex-wrap content-start items-center justify-center gap-x-7 gap-y-8">
        <AnimatePresence>
          {pins.map((pin, i) => (
            <motion.div
              key={pin.key}
              layout
              initial={{ opacity: 0, y: -28, scale: 1.08, rotate: pin.tilt * 2 }}
              animate={{ opacity: 1, y: 0, scale: 1, rotate: pin.tilt }}
              transition={{
                type: "spring",
                stiffness: 260,
                damping: 18,
                delay: i >= firstProfilePin ? (i - firstProfilePin) * 0.14 : 0,
              }}
              className={pin.className}
            >
              {pin.node}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
