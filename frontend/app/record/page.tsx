'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  AlertTriangle, ArrowRight, CheckCircle2, Circle, Globe, Keyboard,
  Loader2, MousePointerClick, Play, Save, Square, X,
} from 'lucide-react'
import { api, BrowserType, RecordingSession, RecordedEvent } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import BrowserSelect from '@/components/BrowserSelect'

const ACTION_STYLES: Record<string, string> = {
  navigate: 'text-sky-300 bg-sky-500/10 border-sky-500/30',
  click: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30',
  submit: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30',
  type: 'text-amber-300 bg-amber-500/10 border-amber-500/30',
  select: 'text-violet-300 bg-violet-500/10 border-violet-500/30',
  check: 'text-violet-300 bg-violet-500/10 border-violet-500/30',
  upload: 'text-rose-300 bg-rose-500/10 border-rose-500/30',
  press: 'text-cyan-300 bg-cyan-500/10 border-cyan-500/30',
}

const STATUS_LABEL: Record<string, string> = {
  starting: 'Brauzer ochilmoqda…',
  recording: 'Yozib olinmoqda',
  stopped: "To'xtatilgan",
  closed: 'Brauzer yopildi',
  error: 'Xatolik',
}

function eventIcon(action: string) {
  if (action === 'navigate') return <Globe size={14} />
  if (action === 'type' || action === 'press') return <Keyboard size={14} />
  return <MousePointerClick size={14} />
}

function hostOf(value: string | undefined) {
  try {
    return value ? new URL(value).host : 'brauzer'
  } catch {
    return 'brauzer'
  }
}

export default function RecordPage() {
  const { user } = useAuth()
  const router = useRouter()

  const [session, setSession] = useState<RecordingSession | null>(null)
  const [url, setUrl] = useState('https://')
  const [browser, setBrowser] = useState<BrowserType>('chromium')
  const [name, setName] = useState('')
  const [starting, setStarting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const listRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!user) router.replace('/login')
  }, [user, router])

  // Serverda ochiq sessiya bo'lsa (masalan, sahifa yangilangan), unga
  // qayta ulanamiz — yozilgan harakatlar yo'qolmaydi.
  useEffect(() => {
    if (!user) return
    let alive = true
    api.activeRecording()
      .then((res) => {
        if (!alive) return
        if (res?.active && res.session) setSession(res.session)
      })
      .catch(() => {})
    return () => { alive = false }
  }, [user])

  const refresh = useCallback(async (sessionId: string) => {
    try {
      const data = await api.getRecording(sessionId)
      setSession(data)
      if (data.status !== 'recording' && data.status !== 'starting') {
        setError('')
      }
    } catch (e: any) {
      if (e?.status === 404) {
        setSession(null)
        setNotice("Yozib olish sessiyasi tugadi. Yangidan boshlang.")
      }
    }
  }, [])

  useEffect(() => {
    if (!session) return
    if (session.status !== 'recording' && session.status !== 'starting') return
    const timer = setInterval(() => { refresh(session.id) }, 1500)
    return () => clearInterval(timer)
  }, [session, refresh])

  const eventsCount = session?.events.length ?? 0
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [eventsCount])

  async function start() {
    const value = url.trim()
    if (!value || value === 'https://' || value === 'http://') {
      setError('Boshlang‘ich URL kiriting (masalan, https://online-mahalla.uz).')
      return
    }
    setStarting(true)
    setError('')
    setNotice('')
    try {
      const data = await api.startRecording(value, browser)
      setSession(data)
      setName('')
    } catch (e: any) {
      setError(e?.message || 'Brauzerni ochib bo‘lmadi')
    } finally {
      setStarting(false)
    }
  }

  async function save() {
    if (!session) return
    if (!confirm(
      'Brauzer yopiladi va hozirgacha yozilgan harakatlar shablon sifatida saqlanadi. Davom etamizmi?'
    )) return
    setSaving(true)
    setError('')
    try {
      const job = await api.stopRecording(session.id, name.trim() || undefined)
      router.push(`/job?id=${encodeURIComponent(job.id)}`)
    } catch (e: any) {
      setError(e?.message || 'Shablonni saqlab bo‘lmadi')
      setSaving(false)
    }
  }

  async function cancel() {
    if (!session) return
    if (!confirm('Yozib olingan barcha harakatlar va screenshotlar o‘chiriladi. Bekor qilamizmi?')) return
    setCancelling(true)
    setError('')
    try {
      await api.cancelRecording(session.id)
      setSession(null)
      setNotice('Yozuv bekor qilindi. Yangi yozuv boshlashingiz mumkin.')
    } catch (e: any) {
      setError(e?.message || 'Bekor qilib bo‘lmadi')
    } finally {
      setCancelling(false)
    }
  }

  if (!user) {
    return (
      <div className="flex-1 mx-auto max-w-7xl px-4 py-10">
        <p className="text-slate-400">Sahifani ko‘rish uchun tizimga kiring…</p>
      </div>
    )
  }

  const busy = starting || saving || cancelling
  const isLive = session?.status === 'recording' || session?.status === 'starting'
  const closedByUser = session?.status === 'closed' || session?.status === 'error'

  return (
    <main className="flex-1 mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-6 rounded-2xl border border-slate-800 bg-slate-900/70 p-6">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-400">
              Universal rejim
            </p>
            <h1 className="mt-1 text-2xl font-bold text-white sm:text-3xl">
              Brauzerdan shablon yozib olish
            </h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-400">
              Video tahlili shart emas: brauzerda kerakli saytga kirib, amallarni
              o‘zingiz bajaring. Dastur har bir bosish, yozish va tanlashni
              shablonga aylantiradi — keyin uni istalgan vaqt qayta bajarish mumkin.
            </p>
          </div>
          <span className="inline-flex w-fit items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs font-semibold text-emerald-300">
            <Circle size={8} className="fill-emerald-400 text-emerald-400" /> Live yozuv
          </span>
        </div>
      </header>

      {error && (
        <div className="mb-4 flex items-start gap-2 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {error}
        </div>
      )}
      {notice && (
        <div className="mb-4 flex items-start gap-2 rounded-xl border border-sky-500/30 bg-sky-500/10 px-4 py-3 text-sm text-sky-200">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {notice}
        </div>
      )}

      {!session ? (
        <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
          <section className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6">
            <h2 className="mb-4 text-lg font-semibold text-white">1. Saytni tanlang</h2>
            <label className="mb-1 block text-xs text-slate-500">Boshlang‘ich URL</label>
            <input
              className="input"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com"
              spellCheck={false}
              onKeyDown={(e) => { if (e.key === 'Enter' && !busy) start() }}
            />
            <div className="mt-4">
              <BrowserSelect value={browser} onChange={setBrowser} disabled={busy} />
            </div>
            <button
              type="button"
              onClick={start}
              disabled={busy}
              className="btn-primary mt-5 w-full px-4 py-2.5 disabled:opacity-40"
            >
              {starting ? <Loader2 size={16} className="mr-2 animate-spin" /> : <Play size={16} className="mr-2" />}
              {starting ? 'Brauzer ochilmoqda…' : 'Brauzerni ochish'}
            </button>
            <p className="mt-3 text-[11px] text-slate-500">
              Brauzer alohida oynada ochiladi. Uni yopmang — yozuv davom etishi
              uchun oynadan foydalanib turing.
            </p>
          </section>

          <section className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6">
            <h2 className="mb-4 text-lg font-semibold text-white">Qanday ishlaydi?</h2>
            <ol className="space-y-3 text-sm text-slate-300">
              <li className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-xs font-bold text-emerald-300">1</span>
                Brauzer oynasida saytga o‘zingiz kirib, odatdagidek ishlang.
              </li>
              <li className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-xs font-bold text-emerald-300">2</span>
                Bosishlar, matn kiritish, tanlov va sahifa o‘tishlari avtomatik yoziladi.
              </li>
              <li className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-xs font-bold text-emerald-300">3</span>
                «To‘xtatib saqlash»ni bosing — shablon yaratiladi va uni istalgan vaqtda qayta ishga tushirish mumkin.
              </li>
            </ol>
            <div className="mt-5 rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-[11px] leading-relaxed text-emerald-200/80">
              🔒 Parol maydonlari hech qachon yozilmaydi. Ular shablonda
              {' '}<code className="rounded bg-slate-800 px-1">{'{{PASSWORD}}'}</code>{' '}
              sifatida qoladi va har bir ishga tushirishda sizdan so‘raladi.
            </div>
          </section>
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1.25fr_0.75fr]">
          <section className="rounded-2xl border border-slate-800 bg-slate-900/80">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
              <div className="flex items-center gap-2">
                <span className={`relative flex h-2.5 w-2.5 ${isLive ? '' : 'opacity-60'}`}>
                  {isLive && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />}
                  <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${isLive ? 'bg-emerald-400' : 'bg-slate-500'}`} />
                </span>
                <span className="text-sm font-semibold text-white">
                  {STATUS_LABEL[session.status] ?? session.status}
                </span>
                <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-semibold text-slate-300">
                  {eventsCount} harakat
                </span>
              </div>
              <span className="max-w-[46%] truncate text-[11px] text-slate-500" title={session.current_url}>
                {session.current_url || session.start_url}
              </span>
            </div>

            {closedByUser && (
              <div className="mx-4 mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                {session.error || 'Brauzer oynasi yopilgan. Yozilgan harakatlarni saqlash yoki bekor qilish mumkin.'}
              </div>
            )}

            <div ref={listRef} className="max-h-[26rem] overflow-y-auto px-4 py-3">
              {eventsCount === 0 ? (
                <div className="flex flex-col items-center gap-2 py-10 text-center text-sm text-slate-500">
                  <MousePointerClick size={22} className="text-slate-600" />
                  Brauzer oynasida amallarni bajaring — ular shu yerda paydo bo‘ladi.
                </div>
              ) : (
                <ol className="space-y-2">
                  {session.events.map((event: RecordedEvent) => (
                    <li key={event.seq} className="flex items-start gap-3 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
                      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-slate-700 text-[10px] font-mono text-slate-400">
                        {event.seq}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${ACTION_STYLES[event.action] ?? 'text-slate-300 bg-slate-800 border-slate-700'}`}>
                            {eventIcon(event.action)} {event.action}
                          </span>
                          {event.input_required && (
                            <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300">
                              {event.input_key ?? 'input'}
                            </span>
                          )}
                          <span className="text-[10px] text-slate-600">{event.ts}</span>
                        </div>
                        <div className="mt-1 text-sm text-slate-200">{event.description}</div>
                        {event.selector && event.action !== 'navigate' && (
                          <div className="mt-0.5 truncate font-mono text-[10px] text-slate-500" title={event.selector}>
                            {event.selector}
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 px-4 py-3">
              <button
                type="button"
                onClick={cancel}
                disabled={busy}
                className="btn-ghost px-3 py-2 text-sm text-red-300 hover:bg-red-500/10 disabled:opacity-40"
              >
                {cancelling ? <Loader2 size={15} className="mr-1 animate-spin" /> : <X size={15} className="mr-1" />}
                Bekor qilish
              </button>
              <span className="text-[11px] text-slate-500">
                Yozuvni tugatish uchun o‘ngdagi tugmani bosing
              </span>
            </div>
          </section>

          <aside className="space-y-4">
            <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-5">
              <h2 className="mb-1 text-lg font-semibold text-white">Shablonni saqlash</h2>
              <p className="mb-4 text-xs text-slate-500">
                Saqlanganda brauzer yopiladi. Harakatlar ro‘yxati qadamlar
                sifatida saqlanadi va «Ishga tushirish» bilan qayta bajariladi.
              </p>
              <label className="mb-1 block text-xs text-slate-500">Shablon nomi (ixtiyoriy)</label>
              <input
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={session ? `Brauzer yozuvi — ${hostOf(session.start_url)}` : ''}
              />
              <button
                type="button"
                onClick={save}
                disabled={busy}
                className="btn-primary mt-4 w-full px-4 py-2.5 disabled:opacity-40"
              >
                {saving ? <Loader2 size={16} className="mr-2 animate-spin" /> : <Save size={16} className="mr-2" />}
                {saving ? 'Saqlanmoqda…' : 'To‘xtatib saqlash'}
              </button>
              {isLive && (
                <p className="mt-2 flex items-center gap-1 text-[11px] text-slate-500">
                  <Square size={10} /> Saqlashdan oldin brauzerdagi sayt holati
                  muhim emas — faqat yozilgan harakatlar saqlanadi.
                </p>
              )}
            </div>

            <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-5 text-xs text-slate-400">
              <h3 className="mb-2 text-sm font-semibold text-white">Maslahatlar</h3>
              <ul className="space-y-2">
                <li className="flex gap-2"><ArrowRight size={12} className="mt-0.5 shrink-0 text-emerald-400" /> Har bir sahifa o‘tishidan oldin kerakli amallarni to‘liq bajaring.</li>
                <li className="flex gap-2"><ArrowRight size={12} className="mt-0.5 shrink-0 text-emerald-400" /> Ortiqcha bosishlar ham yoziladi — ularni keyin qadamlar ro‘yxatida tahrirlash mumkin.</li>
                <li className="flex gap-2"><ArrowRight size={12} className="mt-0.5 shrink-0 text-emerald-400" /> Fayl yuklashda yo‘l shablonda <code className="rounded bg-slate-800 px-1">{'{{FILE}}'}</code> bo‘lib qoladi.</li>
              </ul>
            </div>
          </aside>
        </div>
      )}
    </main>
  )
}
