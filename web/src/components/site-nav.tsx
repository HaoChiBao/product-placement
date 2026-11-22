import Link from "next/link"

const links = [
  { href: "/watch", label: "Watch" },
  { href: "/provider", label: "Provider" },
  { href: "/brand", label: "Brand" },
  { href: "/", label: "Lab" },
]

export function SiteNav({ tone = "dark" }: { tone?: "dark" | "light" }) {
  const light = tone === "light"
  return (
    <nav
      className={`flex flex-wrap items-center gap-4 px-5 py-4 text-sm ${
        light ? "text-zinc-700" : "text-zinc-300"
      }`}
    >
      <Link
        href="/watch"
        className={`font-[family-name:var(--font-display)] text-lg tracking-tight ${
          light ? "text-zinc-900" : "text-white"
        }`}
      >
        PlaceStream
      </Link>
      <div className="flex flex-1 flex-wrap gap-3">
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={
              light
                ? "hover:text-zinc-900"
                : "text-zinc-400 hover:text-white"
            }
          >
            {l.label}
          </Link>
        ))}
      </div>
    </nav>
  )
}
