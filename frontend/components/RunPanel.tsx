'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, Job, BrowserType } from '@/lib/api'
import BrowserSelect from '@/components/BrowserSelect'
import { useJobWS } from '@/lib/ws'
import { Play, Loader2, CheckCircle2, AlertCircle } from 'lucide-react'

export default function RunPanel({ job, onComplete }: { job: Job; onComplete?: () => void }) {
  const [inputs, setInputs] = useState<Record<string, string>>({})
  const [runId, setRunId] = useState<string | null>(null)
  const [browser, setBrowser] = useState<BrowserType>('chromium')
  const { logs, status, currentStep, errorStep, scrollRef } = useJobWS(job.id)

  useEffect(() => {
    const fields = new Set<string>();
    job.steps.forEach((s) => {
      const matches = s.value_template?.match(/\{\{\s*(\w+)\s*\}\}/g) || [];
      matches.forEach((p) => fields.add(p.replace(/[{}]/g, '').trim()));
    });
    const initial: Record<string, string> = {};
    Array.from(fields).forEach((f) => {
      initial[f] = '';
    });
    setInputs(initial);
  }, [job.steps]);

  async function run() {
    setRunId(null)
    try {
      const res = await api.startRun(job.id, inputs, browser)
      setRunId(res.id)
    } catch (e: any) {
      // error will appear via WS
    }
  }

  const scrollToBottom = useCallback((el: HTMLDivElement | null) => {
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {Object.entries(inputs).map(([key, val]) => (
          <label key={key} className="text-sm block">
            <span className="text-slate-400 block mb-1">{key}</span>
            <input
              className="input"
              value={val}
              onChange={(e) => setInputs({ ...inputs, [key]: e.target.value })}
              placeholder={job.steps.find((s) => s.input_key === key)?.value_hint ?? 'qiymat kiriting'}
            />
          </label>
        ))}
      </div>

      <BrowserSelect value={browser} onChange={setBrowser} disabled={status === 'running'} />

      <button
        onClick={run}
        disabled={status === 'running'}
        className="btn-primary w-full"
      >
        {status === 'running' ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin mr-2" />
            Bajarilmoqda…
          </>
        ) : (
          <>
            <Play size={18} />
            Workflowni ishga tushirish
          </>
        )}
      </button>

      <div className="flex items-center gap-3 text-sm">
        <span className="text-slate-400">Holat:</span>
        <span className={`px-2.5 py-0.5 rounded text-[10px] font-semibold ${
          status === 'completed' ? 'bg-emerald-600 text-white' :
          status === 'failed' ? 'bg-red-600 text-white' :
          status === 'running' ? 'bg-amber-600 text-white' : 'bg-slate-700 text-slate-300'
        }`}>
          {status === 'completed' ? 'Bajarildi' : status === 'failed' ? 'Xato' : status === 'running' ? 'Bajarilmoqda' : 'Kutmoqda'}
        </span>
        {currentStep !== undefined && <span className="text-slate-400">Qadam #{currentStep + 1}</span>}
      </div>

      {status === 'failed' && errorStep !== undefined && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3">
          <div className="flex items-start gap-2 mb-1">
            <AlertCircle size={16} className="text-red-400" />
            <span className="text-sm text-red-300 font-medium">Xato bosqich #{errorStep + 1}</span>
          </div>
          <button
            onClick={() => api.reanalyze(job.id).then(() => onComplete?.())}
            className="text-xs text-emerald-400 hover:underline mt-1"
          >
            Qayta tahlil qiling
          </button>
        </div>
      )}

      {status === 'completed' && (
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3">
          <div className="flex items-start gap-2">
            <CheckCircle2 size={16} className="text-emerald-400" />
            <span className="text-sm text-emerald-300 font-medium">Barcha bosqichlar muvaffaqiyatli bajarildi</span>
          </div>
        </div>
      )}

      <div className="h-64 overflow-y-auto bg-black/40 border border-slate-800 rounded p-3 font-mono text-xs">
        <div ref={scrollRef} className="space-y-1">
          {logs.map((l, i) => <div key={i}>{l}</div>)}
          {!logs.length && <div className="text-slate-600">Loglar shu yerda ko‘rinadi…</div>}
        </div>
      </div>
    </div>
  )
}
