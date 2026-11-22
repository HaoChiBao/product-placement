import { SiteNav } from "@/components/site-nav"
import { SuggestApp } from "@/components/suggest-app"

export default function HomePage() {
  return (
    <main className="min-h-screen">
      <SiteNav tone="light" />
      <SuggestApp />
    </main>
  )
}
