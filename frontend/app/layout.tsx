import type { Metadata, Viewport } from "next"
import "./globals.css"
import { AuthProvider } from "@/lib/auth"
import Navbar from "@/components/Navbar"

export const metadata: Metadata = {
  title: "VideoWorkflow Studio",
  description: "Video yuklang, uzdagi amallarni aniqlang va browser workflow avtomatik bajarilsin.",
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="uz" className="scroll-smooth">
      <body className="flex flex-col font-sans antialiased">
        <AuthProvider>
          <Navbar />
          {children}
        </AuthProvider>
      </body>
    </html>
  )
}
