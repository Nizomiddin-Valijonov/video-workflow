'use client'
import { useState } from 'react'
import { Step, api } from '@/lib/api'
import { copyText } from '@/lib/clipboard'
import { Edit, Copy, Trash2 } from 'lucide-react'

export default function StepList({ steps, onChanged }: { steps: Step[]; onChanged?: () => void }) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Partial<Step>>({})
  const [err, setErr] = useState('')

  function startEdit(step: Step) {
    setEditingId(step.id)
    setErr('')
    setDraft({
      action: step.action,
      selector: step.selector,
      value_template: step.value_template,
      value_hint: step.value_hint,
      description: step.description,
      input_required: step.input_required,
      input_key: step.input_key,
    })
  }

  function cancelEdit() {
    setEditingId(null)
    setDraft({})
    setErr('')
  }

  async function saveEdit(step: Step) {
    try {
      await api.updateStep(step.id, draft)
      setEditingId(null)
      setDraft({})
      onChanged?.()
    } catch (e: any) {
      setErr(e?.message || 'Saqlash xatosi')
    }
  }

  async function deleteStepJob(step: Step) {
    try {
      await api.deleteJob(step.job_id)
      onChanged?.()
    } catch (e: any) {
      setErr(e?.message || "O'chirish xatosi")
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-[0.15em]">Bozor qadamlar ro'yxati</h3>
        <span className="text-[10px] text-slate-500">{steps.length} ta bosqich</span>
      </div>
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 overflow-hidden">
        {steps.length === 0 ? (
          <div className="p-4 text-center text-sm text-slate-500">Bosqichlar topilmadi.</div>
        ) : (
          steps.map((s, i) => (
            <div key={s.id} className="group flex items-center gap-3 px-4 py-3 border-b border-slate-800 last:border-0 hover:bg-slate-800/30 transition">
              <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-slate-600 text-[10px] font-mono text-slate-400">
                {s.order_index + 1}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 text-xs font-mono text-slate-400 mb-0.5">
                  <span className={`bg-slate-800 px-1.5 rounded ${actionColor(s.action)}`}>{s.action}</span>
                  {s.input_required && <span className="bg-amber-500/10 text-amber-400 px-1.5 rounded">input kerak</span>}
                </div>
                <div className="text-sm text-slate-200 truncate">{s.description}</div>
                {s.selector && (
                  <div className="text-[10px] text-slate-500 truncate">{s.selector}</div>
                )}
              </div>
              <div className="flex items-center gap-1.5">
                {editingId === s.id ? (
                  <>
                    <button onClick={() => saveEdit(s)} className="btn-primary px-2 py-1.5 text-xs">
                      <Edit size={12} className="mr-1" /> Saqlash
                    </button>
                    <button onClick={cancelEdit} className="btn-ghost px-2 py-1.5 text-xs">
                      Bekor
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      title="Tahrirlash"
                      onClick={() => startEdit(s)}
                      className="btn-ghost p-1.5 hover:text-emerald-400"
                    >
                      <Edit size={14} />
                    </button>
                    <button
                      title="Nusxalash"
                      onClick={() => copyText(s.selector ?? '').catch(() => {})}
                      className="btn-ghost p-1.5 hover:text-sky-400"
                    >
                      <Copy size={14} />
                    </button>
                    <button
                      title="O'chirish"
                      onClick={() => deleteStepJob(s)}
                      className="btn-ghost p-1.5 hover:text-red-400"
                    >
                      <Trash2 size={14} />
                    </button>
                  </>
                )}
              </div>
            </div>
          ))
        )}
      </div>
      {err && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{err}</div>
      )}
    </div>
  )
}

function actionColor(a: string) {
  const map: Record<string, string> = {
    navigate: 'text-sky-400 border-sky-500/30',
    click: 'text-emerald-400 border-emerald-500/30',
    type: 'text-amber-400 border-amber-500/30',
    select: 'text-violet-400 border-violet-500/30',
    upload: 'text-rose-400 border-rose-500/30',
    submit: 'text-emerald-400 border-emerald-500/30',
    wait: 'text-slate-400 border-slate-600',
  }
  return map[a] ?? 'text-slate-400 border-slate-600'
}
