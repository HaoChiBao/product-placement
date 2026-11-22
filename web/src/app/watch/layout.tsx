import type { ReactNode } from "react"

import "./watch.css"

export default function WatchLayout({ children }: { children: ReactNode }) {
  return <div className="watch">{children}</div>
}
