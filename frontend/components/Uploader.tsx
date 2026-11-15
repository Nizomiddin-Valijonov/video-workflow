'use client'
import { useState } from 'react'
import { api } from '@/lib/api'

export default function Uploader({ onUploaded }: { onUploaded: (j: any) => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [name, setName] = useState('')

  async function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true); setErr(''); setName(file.name)
    try {
      const job = await api.uploadVideo(file)
      onUploaded(job)
    } catch (error: any) {
      setErr(error?.message || 'Yuklash xatosi')
    } finally {
      setBusy(false)
      e.target.value = ''
    }
  }

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6 shadow-xl">
      <label className="group block cursor-pointer rounded-2xl border border-dashed border-slate-700 bg-slate-950/60 p-8 text-center transition hover:border-emerald-500 hover:bg-slate-950">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-emerald-500/10 text-2xl text-emerald-400">🎬</div>
        <div className="text-lg font-semibold text-slate-100">{busy ? 'Yuklanmoqda…' : 'Video faylni tanlang'}</div>
        <div className="mt-2 text-sm text-slate-400">MP4, MOV, WEBM, MKV</div>
        <input type="file" accept="video/*" onChange={handleChange} disabled={busy} className="sr-only" />
      </label>
      {name && (
        <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-sm text-slate-300">
          Tanlangan: <span className="font-medium text-slate-100">{name}</span>
        </div>
      )}
      {err && (
        <div className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{err}</div>
      )}
    </div>
  )
}
