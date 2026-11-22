import type { Metadata } from "next"
import { IBM_Plex_Mono, Outfit, Syne } from "next/font/google"

import "./globals.css"

const syne = Syne({
  subsets: ["latin"],
  variable: "--font-display",
})

const outfit = Outfit({
  subsets: ["latin"],
  variable: "--font-sans",
})

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
})

export const metadata: Metadata = {
  title: "PlaceStream · product placement",
  description:
    "Provider catalog, brand placements, and a consumer watch experience",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body
        className={`${syne.variable} ${outfit.variable} ${plexMono.variable} min-h-screen font-sans antialiased`}
      >
        {children}
      </body>
    </html>
  )
}
