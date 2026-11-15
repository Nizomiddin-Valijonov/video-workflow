'use client'
import { useEffect, useState } from 'react'
import { api, Job } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useRouter } from 'next/navigation'
import Uploader from '@/components/Uploader'

export default function Home() {
  const { user } = useAuth()
  const router = useRouter()
  const [job, setJob] = useState<Job | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])

  useEffect(() => {
    if (!user) { router.replace('/login'); return }
    load()
  }, [user])

  async function load() {
    const data = await api.listJobs()
    setJobs(data.items ?? [])
  }

  useEffect(() => {
    if (!job) return
    if (['analyzed', 'failed'].includes(job.status)) return
    const t = setInterval(async () => {
      try { setJob(await api.getJob(job.id)) } catch {}
    }, 3000)
    return () => clearInterval(t)
  }, [job])

  async function selectJob(item: Job) {
    setJob(item)
    router.push(`/job?id=${encodeURIComponent(item.id)}`)
  }

  const steps = job?.steps ?? []
  const pageHint = steps.find((s) => s.meta?.url_guess)?.meta?.url_guess ?? '—'
  const totalElements = steps.reduce((sum, s) => sum + (s.meta?.elements?.length || 0), 0)

  return (
    <main className="flex-1 mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
      <header className="mb-8 rounded-3xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="text-sm font-medium uppercase tracking-[0.2em] text-emerald-400">Workflow Avtomatizatsiya</p>
            <h1 className="mt-2 text-3xl font-semibold text-white sm:text-4xl">Video yoki brauzer → Shablon</h1>
            <p className="mt-2 max-w-2xl text-sm text-slate-400">
              Videoni yuklang yoki brauzerda amallarni ko‘rsatib bering — dastur
              ularni qayta bajariladigan shablonga aylantiradi.
            </p>
          </div>
          <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-xs font-semibold text-emerald-300">
            Production-ready
          </span>
        </div>
      </header>

      <section className="grid gap-6 lg:grid-cols-[1.3fr_0.9fr]">
        <div className="space-y-6">
          <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6 shadow-xl">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-xl font-semibold text-white">1. Video yuklang</h2>
              <span className="text-xs text-slate-500">yoki pastdagi brauzer rejimidan foydalaning</span>
            </div>
            <Uploader onUploaded={async (j: Job) => {
              setJob(j)
              setJobs((prev) => [j, ...prev])
              router.push(`/job?id=${encodeURIComponent(j.id)}`)
            }} />
            <button
              type="button"
              onClick={() => router.push('/record')}
              className="mt-4 flex w-full items-center justify-between rounded-2xl border border-sky-800/60 bg-sky-950/30 px-5 py-4 text-left transition hover:border-sky-500/70 hover:bg-sky-950/50"
            >
              <span>
                <span className="flex items-center gap-2 text-sm font-semibold text-sky-200">
                  <span className="text-lg">🖱️</span> Brauzerdan yozib olish
                </span>
                <span className="mt-1 block text-xs text-slate-400">
                  Video yo‘qmi? Saytda amallarni o‘zingiz bajarib, shablon yarating.
                </span>
              </span>
              <span className="rounded-full bg-sky-500/20 px-3 py-1 text-xs font-semibold text-sky-300">
                Ochish →
              </span>
            </button>
          </div>

          {job && (
            <div className="space-y-4">
              <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-4">
                <div className="mb-3 flex items-center justify-between gap-4">
                  <h2 className="text-xl font-semibold text-white">2. Aniqlangan workflow</h2>
                  <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.15em] ${
                    job.status === 'analyzed' ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40' :
                    job.status === 'failed' ? 'bg-red-500/20 text-red-400 border-red-500/40' : 'bg-slate-700 text-slate-400 border-slate-700'
                  }`}>
                    {job.status}
                  </span>
                </div>
                <div className="grid gap-3 md:grid-cols-3">
                  <div className="rounded-xl border border-slate-800 bg-slate-950 p-3">
                    <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Bosqichlar</div>
                    <div className="mt-2 text-2xl font-semibold text-white">{steps.length}</div>
                  </div>
                  <div className="rounded-xl border border-slate-800 bg-slate-950 p-3">
                    <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Sahifa ipkuvi</div>
                    <div className="mt-2 text-sm text-sky-300 break-all">{pageHint}</div>
                  </div>
                  <div className="rounded-xl border border-slate-800 bg-slate-950 p-3">
                    <div className="text-xs uppercase tracking-[0.2em] text-slate-500">UI elementlar</div>
                    <div className="mt-2 text-2xl font-semibold text-white">{totalElements}</div>
                  </div>
                </div>
                <StepListMini steps={steps} onSelect={selectJob} />
              </div>
            </div>
          )}
        </div>

        <aside className="rounded-2xl border border-slate-800 bg-slate-900/80 p-4">
          <h2 className="mb-3 text-lg font-semibold text-white">Oxirgi jobbalar</h2>
          <div className="space-y-2">
            {jobs.length === 0 ? (
              <div className="text-sm text-slate-500">Hali shablon yaratilmagan.</div>
            ) : (
              jobs.map((item) => (
                <button
                  key={item.id}
                  onClick={() => selectJob(item)}
                  className={`w-full rounded-xl border p-3 text-left transition ${
                    job?.id === item.id
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
                    <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
                      item.source === 'recorded'
                        ? 'bg-sky-500/10 text-sky-300'
                        : 'bg-slate-800 text-slate-400'
                    }`}>{item.source === 'recorded' ? 'brauzer' : 'video'}</span>
                    <span>{item.steps?.length ?? 0} bosqich</span>
                  </div>
                </button>
              ))
            )}
          </div>
        </aside>
      </section>
    </main>
  )
}

function StepListMini({ steps, onSelect }: { steps: any[]; onSelect: (j: any) => void }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 overflow-hidden">
      {steps.length === 0 ? (
        <div className="p-4 text-center text-sm text-slate-500">Bosqichlar topilmadi.</div>
      ) : (
        steps.map((s, i) => (
          <div key={s.id} className="flex items-center gap-3 px-4 py-3 border-b border-slate-800 last:border-0 hover:bg-slate-800/30 cursor-pointer group" onClick={() => onSelect({ id: s.job_id, filename: '', status: '', steps, error: null })}>
            <div className="flex h-6 w-6 items-center justify-center rounded-full border border-slate-600 text-[10px] font-mono text-slate-400 shrink-0">{i + 1}</div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 text-xs font-mono text-slate-400 mb-0.5">
                <span className="bg-slate-800 px-1.5 rounded text-slate-400">{s.action}</span>
                {s.input_required && <span className="bg-amber-500/10 text-amber-400 px-1.5 rounded text-[10px]">input kerak</span>}
              </div>
              <div className="text-sm text-slate-200 truncate">{s.description}</div>
            </div>
          </div>
        ))
      )}
    </div>
  )
}
