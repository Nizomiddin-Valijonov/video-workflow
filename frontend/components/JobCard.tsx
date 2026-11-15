'use client'
import { Job } from '@/lib/api'
import { Play, Clock, Video } from 'lucide-react'

export default function JobCard({ item, active, onSelect }: { item: Job; active: boolean; onSelect: () => void }) {
  return (
    <button
      onClick={onSelect}
      className={`w-full rounded-xl border p-3 text-left transition ${
        active
          ? 'border-emerald-500 bg-emerald-500/10 text-white'
          : 'border-slate-800 bg-slate-950 text-slate-300 hover:border-slate-700'
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="truncate text-sm font-medium">{item.filename}</span>
        <span className={`text-[10px] uppercase tracking-[0.2em] ${
          item.status === 'analyzed' ? 'text-emerald-400' :
          item.status === 'failed' ? 'text-red-400' :
          item.status === 'running' ? 'text-amber-400' :
          'text-slate-400'
        }`}>{item.status}</span>
      </div>
      <div className="flex items-center gap-2 text-[11px] text-slate-500">
        <Video size={12} />
        <span>{item.steps?.length ?? 0} bosqich</span>
      </div>
    </button>
  )
}
