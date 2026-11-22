import type { Metadata } from "next"
import { Bebas_Neue, Bricolage_Grotesque, Geist, Geist_Mono } from "next/font/google"

import "./globals.css"

const display = Bricolage_Grotesque({
  subsets: ["latin"],
  weight: ["500", "700"],
  variable: "--font-display",
})

/** Condensed all-caps wordmark face (free stand-in for Netflix's proprietary logotype). */
const brand = Bebas_Neue({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-brand",
})

const body = Geist({
  subsets: ["latin"],
  variable: "--font-body",
})

const mono = Geist_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
})

export const metadata: Metadata = {
  title: "Unskippable",
  description: "Place your product inside creator videos",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${display.variable} ${brand.variable} ${body.variable} ${mono.variable}`}>
        {children}
      </body>
    </html>
  )
}
