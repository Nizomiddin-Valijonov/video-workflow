'use client'
import { useEffect, useState } from 'react'
import { useAuth } from '@/lib/auth'
import { usePathname, useRouter } from 'next/navigation'
import { LogOut, Layout, MonitorPlay } from 'lucide-react'

export default function Navbar() {
  const { user, token, logout } = useAuth()
  const router = useRouter()
  const pathname = usePathname() || '/'
  const [mounted, setMounted] = useState(false)

  useEffect(() => { setMounted(true) }, [])

  // Statik yig'ilma `trailingSlash` bilan `/login/` ko'rinishida bo'ladi —
  // shuning uchun oxirgi `/` ni olib tashlab solishtiramiz.
  const normalizedPath = pathname.replace(/\/+$/, '') || '/'
  const publicPaths = ['/login', '/register']
  const isPublicPath = publicPaths.includes(normalizedPath)

  // API kalit bo'lmasa va foydalanuvchi kirgan bo'lmasa -> login sahifasiga
  useEffect(() => {
    if (mounted && !token && !user && !isPublicPath) {
      router.replace('/login')
    }
  }, [mounted, token, user, isPublicPath, router])

  if (!mounted) return null
  if (!token && !user && !isPublicPath) return null

  return (
    <nav className="sticky top-0 z-50 border-b border-slate-800 bg-slate-900/90 backdrop-blur-md">
      <div className="mx-auto max-w-7xl px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-600 text-white text-sm font-bold">VW</div>
          <span className="font-bold text-lg tracking-tight text-white">VideoWorkflow</span>
        </div>
        <div className="flex items-center gap-3">
          <a href="/" className="btn-ghost px-3 py-2 text-sm">
            <Layout size={16} className="mr-1" /> Bosh sahifa
          </a>
          <button
            onClick={() => router.push('/record')}
            className={`btn-ghost px-3 py-2 text-sm ${normalizedPath === '/record' ? 'text-emerald-300' : ''}`}
          >
            <MonitorPlay size={16} className="mr-1" /> Yozib olish
          </button>
          <button onClick={logout} className="btn-ghost px-3 py-2 text-sm text-red-300 hover:bg-red-500/10">
            <LogOut size={16} className="mr-1" /> Chiqish
          </button>
        </div>
      </div>
    </nav>
  )
}
