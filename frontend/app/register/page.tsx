'use client'
import { useState } from 'react'
import { register } from '@/lib/api'
import { useAuth } from '@/lib/auth'

export default function RegisterPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const { login } = useAuth()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setErr(''); setBusy(true)
    try {
      const res = await register({ email, password })
      login(res.access_token, { email })
      window.location.href = '/'
    } catch (e: any) {
      setErr(e?.message || 'Ro‘yxatdan o‘tishda xato')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-[calc(100vh-80px)] flex items-center justify-center px-4">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-8 md:p-12 w-full max-w-md shadow-2xl">
        <h2 className="mb-6 text-2xl font-semibold text-white text-center">Ro‘yxatdan o‘tish</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm text-slate-400 mb-1">Email</label>
            <input className="input" type="email" required
              value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label className="block text-sm text-slate-400 mb-1">Parol</label>
            <input className="input" type="password" required
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {err && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{err}</div>}
          <button type="submit" disabled={busy}
            className={`w-full btn py-3 ${busy ? 'opacity-70' : 'btn-primary'}`}>
            {busy ? 'Yaratilmoqda…' : 'Akkaunt yaratish'}
          </button>
        </form>
        <p className="mt-6 text-center text-xs text-slate-500">
          Hisobingiz bormi?{' '}
          <a href="/login" className="text-emerald-400 hover:underline">Kiring</a>
        </p>
      </div>
    </div>
  )
}
