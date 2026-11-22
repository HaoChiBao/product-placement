import Link from "next/link"

export function WatchChrome({
  current = "watch",
  meta,
}: {
  current?: "watch" | "play"
  meta?: string
}) {
  return (
    <header className="watch-top">
      <Link href="/" className="watch-brand" aria-label="Inset home">
        <span className="watch-brand-mark" aria-hidden />
        Inset
      </Link>
      <nav className="watch-nav" aria-label="Watch">
        <Link href="/watch" aria-current={current === "watch" ? "page" : undefined}>
          Watch
        </Link>
        <Link href="/videos">Brand flow</Link>
      </nav>
      <div className="watch-top-sp" />
      {meta ? <div className="watch-top-meta">{meta}</div> : null}
    </header>
  )
}
