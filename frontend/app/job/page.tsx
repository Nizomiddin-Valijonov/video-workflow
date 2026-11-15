'use client'
import { useEffect, useMemo, useState, useCallback, useRef, Suspense } from 'react'
import { api, Job, Step, ExcelPerson, ExcelDataset, BrowserType } from '@/lib/api'
import { useJobWS } from '@/lib/ws'
import { useAuth } from '@/lib/auth'
import { copyText } from '@/lib/clipboard'
import BrowserSelect from '@/components/BrowserSelect'
import { useRouter, useSearchParams } from 'next/navigation'
import { Loader2, Play, RefreshCw, Edit2, Save, X, ImageIcon, Copy, AlertTriangle, FileSpreadsheet, Search, UserCheck, Users } from 'lucide-react'

// Faqat shu maydonlar qo'lda kiritiladi. Qolganlari Excel'dan avtomatik to'ladi.
const MANUAL_FIELDS = ['LOGIN', 'PASSWORD']
const LEGACY_DATASET = 'ХАТЛОВ'

// Sayt bir telefon raqamini 5+ a'zoga biriktirishga ruxsat bermaydi.
// Har bir Excel qatori uchun alohida, barqaror raqam.
function phoneForRow(row: number) {
  const a = 10 + (Math.floor(row / 100) % 90)
  const b = row % 100
  return `+998(99)240-${String(a).padStart(2, '0')}-${String(b).padStart(2, '0')}`
}

// Statik yig'ilma (desktop) uchun job ID endi marshrut emas, query
// parametr: /job?id=<uuid>. Suspense Next 14'da useSearchParams
// uchun majburiy.
export default function JobDetailPage() {
  return (
    <Suspense fallback={
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    }>
      <JobDetailWithParams />
    </Suspense>
  )
}

function JobDetailWithParams() {
  const searchParams = useSearchParams()
  const id = searchParams.get('id') ?? ''
  return <JobDetailView id={id} />
}

function JobDetailView({ id }: { id: string }) {
  const { user } = useAuth()
  const router = useRouter()
  const [job, setJob] = useState<Job | null>(null)
  const [editing, setEditing] = useState<string | null>(null) // step id
  const [stepEdit, setStepEdit] = useState<Partial<Step>>({})
  const [logs, setLogs] = useState<string[]>([])
  const [status, setStatus] = useState<string>('idle')
  const [currentStep, setCurrentStep] = useState<number | undefined>()
  const [errorStep, setErrorStep] = useState<number | undefined>()

  const logBoxRef = useRef<HTMLDivElement | null>(null)
  const scrollRef = useCallback((el: HTMLDivElement | null) => {
    logBoxRef.current = el
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  // So'nggi ma'lum job holati — tahlil davomida polling uchun.
  const jobStatusRef = useRef('')

  const fetchJob = useCallback(async () => {
    if (!id) return
    try {
      const j = await api.getJob(id)
      setJob(j)
      jobStatusRef.current = j?.status ?? ''
    } catch (e: any) {
      const status = typeof e?.status === 'number'
        ? e.status
        : Number(String(e?.message ?? '').match(/\b(401|404)\b/)?.[1] ?? 0)
      if (status === 401 || status === 404) router.push('/login')
    }
  }, [id, router])

  useEffect(() => { fetchJob() }, [fetchJob])

  const {
    logs: wsLogs,
    status: wsStatus,
    currentStep: wsStep,
    errorStep: wsErrStep,
    revision,
  } = useJobWS(id)

  useEffect(() => {
    if (wsStatus) setStatus(wsStatus)
    if (wsStep !== undefined) setCurrentStep(wsStep)
    if (wsErrStep !== undefined) setErrorStep(wsErrStep)
  }, [wsStatus, wsStep, wsErrStep])

  // Tahlil qadamlarni yangilaganda (WS "status"/"step" xabari) sahifa
  // ma'lumotini qayta yuklaymiz — aks holda yangi qadamlar ko'rinmaydi.
  useEffect(() => {
    if (revision > 0) fetchJob()
  }, [revision, fetchJob])

  // Tahlil davomida WS hodisasi kechiksa ham holat yangilanib turadi.
  useEffect(() => {
    if (!id) return
    const timer = setInterval(() => {
      const s = jobStatusRef.current
      if (s === 'pending' || s === 'analyzing') fetchJob()
    }, 2500)
    return () => clearInterval(timer)
  }, [id, fetchJob])

  // WS loglari va mahalliy (UI) loglar alohida saqlanadi — biri
  // ikkinchisini o'chirib yubormasligi uchun renderda birlashtiriladi.
  const allLogs = useMemo(
    () => [...logs, ...wsLogs].slice(-1000),
    [logs, wsLogs]
  )

  useEffect(() => {
    const el = logBoxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [allLogs])

  const steps = useMemo(() => job?.steps ?? [], [job])
  const isRecorded = job?.source === 'recorded'
  const fields = useMemo(() => Array.from(new Set(
    steps.flatMap((s) => (s.value_template?.match(/\{\{\s*(\w+)\s*\}\}/g) || []).map((p) => p.replace(/[{}]/g, '').trim()))
  )), [steps])

  // Har bir parametr uchun birinchi uchragan izoh (masalan, «Fayl yo'li»).
  const fieldHints = useMemo(() => {
    const hints: Record<string, string> = {}
    for (const step of steps) {
      for (const match of step.value_template?.matchAll(/\{\{\s*(\w+)\s*\}\}/g) ?? []) {
        const key = match[1]
        if (!hints[key]) hints[key] = step.value_hint ?? ''
      }
    }
    return hints
  }, [steps])

  const isSecretField = (key: string) => /pass|parol|secret|pwd/i.test(key)
  const isFileField = (key: string) => key.toUpperCase() === 'FILE'

  const willSave = useMemo(
    () => steps.some((s) => (s.selector ?? '').includes('save-btn')),
    [steps]
  )

  const [inputs, setInputs] = useState<Record<string, string>>({})
  const [browser, setBrowser] = useState<BrowserType>('chromium')

  useEffect(() => {
    setInputs((prev) => {
      const next: Record<string, string> = {}
      for (const f of fields) next[f] = prev[f] ?? ''
      return next
    })
  }, [fields])

  // Excel ro'yxati: LOGIN/PASSWORD'dan tashqari maydonlar shu yerdan to'ladi.
  const [excelOpen, setExcelOpen] = useState(true)
  const [excelDatasets, setExcelDatasets] = useState<ExcelDataset[]>([])
  const [excelDataset, setExcelDataset] = useState('')
  const [excelQuery, setExcelQuery] = useState('')
  const [excelResults, setExcelResults] = useState<ExcelPerson[]>([])
  const [excelLoading, setExcelLoading] = useState(false)
  const [excelError, setExcelError] = useState('')
  const [excelUploading, setExcelUploading] = useState(false)
  const [excelUploadError, setExcelUploadError] = useState('')
  const [selectedPerson, setSelectedPerson] = useState<ExcelPerson | null>(null)
  const excelFileRef = useRef<HTMLInputElement>(null)

  // Batch: 0 — cheksiz (barcha ko'chalar). Sinov uchun 1 kiriting.
  const [batchHouseholds, setBatchHouseholds] = useState(0)
  // Xavfsiz sinov: avval 1 odam. Muvaffaqiyatli bo'lsa 5 ga qo'yiladi.
  const [batchPeople, setBatchPeople] = useState(1)
  const [batchStartRow, setBatchStartRow] = useState(262655)
  // Bo'sh bo'lsa: avvalgi holat yo'q bo'lsa birinchi ko'chadan boshlanadi.
  const [batchStartStreet, setBatchStartStreet] = useState('')
  // Birinchi login'dan keyin sessiya saqlanadi; keyingi runlar parolsiz.
  const [reuseSession, setReuseSession] = useState(false)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)

  useEffect(() => {
    api.excelDatasets()
      .then((r) => {
        const items = r?.items ?? []
        setExcelDatasets(items)
        if (items.length) {
          const firstId = items[0].id
          setExcelDataset((prev) => prev || firstId)
          setBatchStartRow((prev) => (
            prev === 262655 && firstId !== LEGACY_DATASET ? 2 : prev
          ))
        }
      })
      .catch(() => { /* Excel bazasi hali yuklanmagan bo'lishi mumkin */ })
  }, [])

  useEffect(() => {
    if (!user) router.replace('/login')
  }, [user, router])

  if (!user) {
    return (
      <div className="flex-1 mx-auto max-w-7xl px-4 py-10">
        <p className="text-slate-400">Sahifani ko'rish uchun tizimga kiring…</p>
      </div>
    )
  }

  async function searchExcel() {
    const q = excelQuery.trim()
    if (!excelDataset || !q) return
    setExcelLoading(true)
    setExcelError('')
    try {
      const res = await api.excelSearch(excelDataset, q)
      const items = res?.items ?? []
      setExcelResults(items)
      if (!items.length) setExcelError("Ma'lumot topilmadi. Qator raqami yoki JSHSHIRni tekshiring.")
    } catch (e: any) {
      setExcelError(e?.message || 'Qidiruv xatosi')
    } finally {
      setExcelLoading(false)
    }
  }

  async function uploadExcel() {
    const file = excelFileRef.current?.files?.[0]
    if (!file) {
      setExcelUploadError('Avval Excel fayl tanlang')
      return
    }
    setExcelUploading(true)
    setExcelUploadError('')
    try {
      const dataset = await api.uploadExcel(file)
      setExcelDatasets((prev) => [
        dataset,
        ...prev.filter((d) => d.id !== dataset.id),
      ])
      setExcelDataset(dataset.id)
      setBatchStartRow(2)
      setExcelResults([])
      setSelectedPerson(null)
      setExcelError('')
      setLogs((l) => [
        ...l,
        `📤 Excel yuklandi: ${dataset.name} (${dataset.rows.toLocaleString()} qator)`,
      ])
      if (excelFileRef.current) excelFileRef.current.value = ''
    } catch (e: any) {
      setExcelUploadError(e?.message || 'Excel yuklash xatosi')
    } finally {
      setExcelUploading(false)
    }
  }

  function applyExcelPerson(p: ExcelPerson) {
    const prevPhone = (inputs.PHONE ?? '').trim()
    setInputs((prev) => ({
      ...prev,
      JSHSHIR: p.pinpp,
      BIRTH_DATE: p.birth_date,
      // Telefon Excel'da yo'q. Standart "000-00-00" saytda 5+ marta
      // ishlatilgani uchun har bir qatorga alohida raqam beramiz.
      PHONE: prevPhone && prevPhone !== '+998(99)000-00-00'
        ? prevPhone
        : phoneForRow(p.row_num),
    }))
    setSelectedPerson(p)
    setLogs((l) => [...l, `📥 Excel'dan tanlandi: ${p.full_name} (#${p.row_num})`])
  }

  async function startRun() {
    const missing = fields.filter((f) => !(inputs[f] ?? '').trim())
    if ((!isRecorded && !selectedPerson) || missing.length > 0) {
      const parts: string[] = []
      if (!isRecorded && !selectedPerson) parts.push("Excel'dan shaxs tanlang")
      if (missing.length) parts.push(`to'ldiring: ${missing.join(', ')}`)
      setLogs((l) => [...l, `⚠️ ${parts.join('; ')}`])
      return
    }
    if (isRecorded) {
      if (!confirm("Shablon brauzerda amallarni bajaradi (saytda ma'lumot saqlashi mumkin). Davom etamizmi?")) return
    } else if (willSave && !confirm("Diqqat: bu run saytda ma'lumotlarni SAQLAYDI — yangi a'zo xonadonga qo'shiladi. Davom etamizmi?")) return
    try {
      const res = await api.startRun(id, inputs, browser)
      setActiveRunId(res?.id ?? null)
      setLogs((l) => [...l, `🚀 Run boshlandi: ${String(res?.id ?? '').slice(0, 8)}`])
    } catch (e: any) {
      setLogs((l) => [...l, `❌ ${e?.message || 'Run xatosi'}`])
    }
  }

  async function stopActiveRun() {
    if (!activeRunId) return
    if (!confirm("Run to'xtatilsinmi? Hozirgacha saqlangan odamlar saytda qoladi.")) return
    try {
      await api.cancelRun(activeRunId)
      setLogs((l) => [...l, "⏹ To'xtatish so'rovi yuborildi"])
    } catch (e: any) {
      setLogs((l) => [...l, `❌ To'xtatish xatosi: ${e?.message || e}`])
    }
  }

  async function startBatch() {
    const login = (inputs.LOGIN ?? '').trim()
    const password = inputs.PASSWORD ?? ''
    if (reuseSession && (!login || !password)) {
      setLogs((l) => [...l, "⚠️ LOGIN/PASSWORD kiritilmagan — sayt sessiyani yopsa avtomatik qayta login ishlamaydi"])
    }
    if (!reuseSession && (!login || !password)) {
      setLogs((l) => [...l, "⚠️ Batch uchun LOGIN va PASSWORD kiriting (yoki saqlangan sessiyani belgilang)"])
      return
    }
    if (!willSave) {
      setLogs((l) => [...l, "⚠️ Bu jobda saqlash qadami yo'q — batch ishlamaydi"])
      return
    }
    if (!excelDataset) {
      setLogs((l) => [...l, "⚠️ Batch uchun Excel dataset tanlang yoki yuklang"])
      return
    }
    const unlimited = batchHouseholds <= 0
    const scope = unlimited
      ? 'Barcha ko‘chalar bo‘ylab cheksiz xonadonga'
      : `${batchHouseholds} ta xonadonga`
    const total = unlimited
      ? 'ko‘chalar yoki odamlar tugaguncha'
      : `${batchHouseholds * batchPeople} ta`
    const startStreet = batchStartStreet.trim()
    const streetText = startStreet
      ? `Boshlanish ko'chasi: ${startStreet}. `
      : "Boshlanish ko'chasi: avvalgi holat yo'q bo'lsa birinchi ko'cha. "
    if (!confirm(
      `${streetText}Diqqat: ${scope} ${batchPeople} tadan YANGI odam SAQLANADI ` +
      `(jami: ${total}). Har bir odam saytga qo'shiladi. Davom etamizmi?`
    )) return

    try {
      const res = await api.startRun(id, {
        LOGIN: login,
        PASSWORD: password,
        BATCH_MODE: 'true',
        DATASET: excelDataset,
        START_ROW: String(batchStartRow),
        START_STREET: startStreet,
        HOUSEHOLDS_LIMIT: String(batchHouseholds),
        PEOPLE_PER_HOUSEHOLD: String(batchPeople),
        REUSE_SESSION: reuseSession ? 'true' : 'false',
      }, browser)
      setActiveRunId(res?.id ?? null)
      setLogs((l) => [...l, `🚀 Batch boshlandi: ${String(res?.id ?? '').slice(0, 8)}`])
    } catch (e: any) {
      setLogs((l) => [...l, `❌ ${e?.message || 'Batch xatosi'}`])
    }
  }

  async function saveStep(step: Step) {
    try {
      const updated = await api.updateStep(step.id, stepEdit)
      setEditing(null)
      setStepEdit({})
      await fetchJob()
      setLogs((l) => [...l, `✏️ Qadam #{updated.order_index + 1} yangilandi`])
    } catch (e: any) {
      setLogs((l) => [...l, `❌ Saqlash xatosi: ${e?.message}`])
    }
  }

  return (
    <div className="flex-1 mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Job #{job?.id.slice(0, 8) ?? '—'}</h1>
          <p className="text-sm text-slate-400">
            {isRecorded && (
              <span className="mr-2 rounded bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-300">
                brauzer yozuvi
              </span>
            )}
            {job?.filename ?? '—'} · {steps.length} bosqich
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => fetchJob()} className="btn-ghost px-3 py-2">
            <RefreshCw size={16} /> Yangilash
          </button>
          {job?.status === 'analyzed' && steps.length > 0 && (
            <button onClick={startRun} className="btn-primary px-4 py-2">
              <Play size={16} className="mr-2" /> Ishga tushirish
            </button>
          )}
        </div>
      </header>

      {/* Status */}
      <div className="mb-6 flex items-center gap-3">
        <span className="text-slate-400 text-sm">Holat:</span>
        <span className={`px-2.5 py-0.5 rounded text-[10px] font-semibold ${
          status === 'completed' ? 'bg-emerald-600 text-white' :
          status === 'failed' ? 'bg-red-600 text-white' :
          status === 'running' ? 'bg-amber-600 text-white' : 'bg-slate-700 text-slate-300'
        }`}>
          {status === 'completed' ? 'Bajarildi' : status === 'failed' ? 'Xato' : status === 'running' ? 'Bajarilmoqda' : (job?.status ?? '—')}
        </span>
        {currentStep !== undefined && steps.length > 0 && (
          <span className="text-slate-400 text-sm">
            Qadam #{Math.min(currentStep, steps.length - 1) + 1}
          </span>
        )}
        {status === 'failed' && errorStep !== undefined && (
          <>
            <AlertTriangle size={14} className="text-red-400 ml-1" />
            <span className="text-xs text-red-400 ml-1">Xato bosqich #{errorStep + 1}</span>
          </>
        )}
      </div>

      {job?.status === 'analyzed' && steps.length > 0 && (
        <div className="mb-6 rounded-xl border border-slate-800 bg-slate-900/60 p-4 sm:max-w-md">
          <BrowserSelect
            value={browser}
            onChange={setBrowser}
            disabled={status === 'running'}
          />
        </div>
      )}

      {/* Input maydonlari */}
      {fields.length > 0 && (
        <div className="mb-6 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h3 className="mb-3 text-sm font-semibold text-slate-300 uppercase tracking-[0.15em]">Foydalanuvchi ma'lumotlari</h3>

          {!isRecorded && (
          <div className="mb-4">
            <button
              type="button"
              onClick={() => setExcelOpen((v) => !v)}
              className="flex w-full items-center justify-between rounded-lg border border-sky-800/50 bg-sky-950/30 px-3 py-2 text-left"
            >
              <span className="flex items-center gap-2 text-xs font-semibold text-sky-300 uppercase tracking-[0.15em]">
                <FileSpreadsheet size={14} /> Excel'dan ma'lumot olish
              </span>
              <span className="text-xs text-sky-400">{excelOpen ? 'Yopish' : 'Ochish'}</span>
            </button>
            {excelOpen && (
              <div className="mt-2 rounded-lg border border-sky-800/50 bg-sky-950/30 p-3">
                <div className="flex flex-col gap-2 sm:flex-row">
                  <input
                    ref={excelFileRef}
                    type="file"
                    accept=".xlsx,.xlsm,.csv"
                    className="input flex-1 text-xs file:mr-3 file:rounded file:border-0 file:bg-sky-700 file:px-3 file:py-1.5 file:text-white"
                  />
                  <button
                    type="button"
                    className="btn-primary px-3 py-2 disabled:opacity-40"
                    onClick={uploadExcel}
                    disabled={excelUploading}
                  >
                    {excelUploading ? <Loader2 size={16} className="animate-spin" /> : <FileSpreadsheet size={16} />}
                    <span className="ml-1">Excel yuklash</span>
                  </button>
                </div>
                {excelUploadError && <p className="mt-2 text-xs text-red-400">{excelUploadError}</p>}
                <p className="mt-2 text-[11px] text-slate-500">
                  .xlsx, .xlsm yoki .csv yuklang. Yuklangandan keyin shu dataset
                  batch uchun ishlatiladi va boshlanish qatori 2 ga o'rnatiladi.
                </p>

                {excelDatasets.length > 0 ? (
                  <>
                    <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                      <select
                        className="input sm:w-52"
                        value={excelDataset}
                        onChange={(e) => {
                          setExcelDataset(e.target.value)
                          setBatchStartRow(2)
                          // Bo'lakka tegishli eski natija/tanlov qolmasin.
                          setExcelResults([])
                          setSelectedPerson(null)
                          setExcelError('')
                        }}
                      >
                        {excelDatasets.map((d) => (
                          <option key={d.id} value={d.id}>
                            {d.name} ({d.rows.toLocaleString()} qator)
                          </option>
                        ))}
                      </select>
                      <input
                        className="input flex-1"
                        value={excelQuery}
                        onChange={(e) => setExcelQuery(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') searchExcel() }}
                        placeholder="Qator raqami, JSHSHIR yoki F.I.Sh."
                      />
                      <button
                        type="button"
                        className="btn-primary px-3 py-2 disabled:opacity-40"
                        onClick={searchExcel}
                        disabled={excelLoading || !excelQuery.trim()}
                      >
                        {excelLoading ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
                        <span className="ml-1">Topish</span>
                      </button>
                    </div>
                    <p className="mt-2 text-[11px] text-slate-500">
                      JSHSHIR, tug'ilgan sana va telefon avtomatik to'ladi. Telefon Excel'da yo'q — har bir odamga alohida raqam yaratiladi.
                    </p>
                    {excelError && <p className="mt-2 text-xs text-red-400">{excelError}</p>}
                    {excelResults.length > 0 && (
                      <div className="mt-3 max-h-56 divide-y divide-slate-800 overflow-y-auto rounded-lg border border-slate-800">
                        {excelResults.map((p) => (
                          <div key={`${p.row_num}-${p.pinpp}`} className="flex items-center gap-3 px-3 py-2 hover:bg-slate-800/40">
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-sm text-slate-200">{p.full_name}</div>
                              <div className="truncate text-[11px] text-slate-500">
                                #{p.row_num} · {p.pinpp} · {p.birth_date} · {p.passport}
                              </div>
                              <div className="truncate text-[11px] text-slate-600">{p.address}</div>
                            </div>
                            <button
                              type="button"
                              className="btn-ghost shrink-0 px-2 py-1 text-xs text-emerald-400"
                              onClick={() => applyExcelPerson(p)}
                            >
                              Tanlash
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <p className="mt-3 text-xs text-amber-400">
                    Hali Excel bazasi yo'q — yuqoridan .xlsx yoki .csv yuklang.
                  </p>
                )}
              </div>
            )}
          </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {fields.filter((f) => isRecorded || MANUAL_FIELDS.includes(f)).map((key) => (
              <div key={key}>
                <label className="block text-xs text-slate-500 mb-1">
                  {isRecorded ? (fieldHints[key] || key) : key}
                </label>
                <input
                  className="input"
                  type={isSecretField(key) ? 'password' : 'text'}
                  autoComplete="off"
                  value={inputs[key] ?? ''}
                  onChange={(e) => setInputs({ ...inputs, [key]: e.target.value })}
                  placeholder={
                    isRecorded
                      ? (isFileField(key)
                          ? 'C:\\papka\\fayl.pdf'
                          : `{{${key}}} uchun qiymat`)
                      : key === 'LOGIN' ? 'OneID login' : key === 'PASSWORD' ? 'OneID parol' : ''
                  }
                />
                {isRecorded && isFileField(key) && (
                  <p className="mt-1 text-[10px] text-slate-500">
                    Kompyuterdagi to‘liq yo‘lni kiriting (masalan, C:\fayl.pdf).
                  </p>
                )}
              </div>
            ))}
          </div>

          {!isRecorded && (selectedPerson ? (
            <div className="mt-3 rounded-lg border border-emerald-800/40 bg-emerald-950/20 px-3 py-2">
              <div className="flex items-center gap-2 text-xs font-medium text-emerald-300">
                <UserCheck size={14} /> {selectedPerson.full_name}
                <span className="text-emerald-600">#{selectedPerson.row_num}</span>
              </div>
              <div className="mt-1 text-[11px] text-slate-400">
                JSHSHIR: {selectedPerson.pinpp} · Tug'ilgan: {selectedPerson.birth_date}
              </div>
              <div className="mt-2 flex items-center gap-2">
                <label className="text-[11px] text-slate-500">Telefon:</label>
                <input
                  className="input h-8 w-52 text-xs"
                  value={inputs.PHONE ?? ''}
                  onChange={(e) => setInputs({ ...inputs, PHONE: e.target.value })}
                  placeholder="+998(99)000-00-00"
                />
              </div>
            </div>
          ) : (
            <p className="mt-3 text-xs text-amber-400">
              Excel'dan shaxs tanlang — JSHSHIR, tug'ilgan sana va telefon avtomatik to'ladi.
            </p>
          ))}

          {isRecorded && fields.length > 0 && (
            <p className="mt-3 text-xs text-sky-300/80">
              Bu qiymatlar shablon bajarilganda maydonlarga kiritiladi.
              Parol maydonlari hech qachon saqlanmaydi.
            </p>
          )}

          {!isRecorded && willSave && (
            <div className="mt-4 rounded-lg border border-amber-700/50 bg-amber-950/20 p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.15em] text-amber-300">
                    <Users size={14} /> Batch: har bir xonadonga yangi odamlar
                  </div>
                  <p className="mt-1 max-w-2xl text-[11px] text-slate-400">
                    Excel'dan odamlar avtomatik olinadi. LOGIN/PASSWORD yuqorida
                    kiritiladi. Sayt har bir odamdan keyin sessiyani yopsa,
                    dastur shu LOGIN/PASSWORD bilan avtomatik qayta kiradi.
                    Avval 1 xonadon va 1 odam bilan sinab ko'ring.
                    Muvaffaqiyatli bo'lsa, «Har biriga»ni 5 ga qo'ying — qolgan
                    4 tasi shu xonadonga qo'shiladi. 300+ xonadonli ko'chada
                    jadvalning keyingi sahifasi avtomatik ochiladi, ko'cha
                    tugagach keyingi ko'chaga o'tiladi. Saytda allaqachon bor
                    odamlar avtomatik o'tkazib yuboriladi.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={startBatch}
                    disabled={status === 'running'}
                    className="btn-primary px-4 py-2 disabled:opacity-40"
                  >
                    <Play size={16} className="mr-2" /> Batchni boshlash
                  </button>
                  {activeRunId && status === 'running' && (
                    <button
                      type="button"
                      onClick={stopActiveRun}
                      className="btn-ghost px-3 py-2 text-red-300"
                    >
                      To'xtatish
                    </button>
                  )}
                </div>
              </div>

              <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
                <label className="block">
                  <span className="mb-1 block text-[11px] text-slate-500">
                    Nechta xonadon (0 = cheksiz)
                  </span>
                  <input
                    type="number"
                    min={0}
                    className="input"
                    value={batchHouseholds}
                    onChange={(e) => setBatchHouseholds(Math.max(0, Number(e.target.value) || 0))}
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-[11px] text-slate-500">Har biriga (yangi odam)</span>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    className="input"
                    value={batchPeople}
                    onChange={(e) => setBatchPeople(Math.max(1, Math.min(10, Number(e.target.value) || 5)))}
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-[11px] text-slate-500">Excel boshlanish qatori</span>
                  <input
                    type="number"
                    min={2}
                    className="input"
                    value={batchStartRow}
                    onChange={(e) => setBatchStartRow(Math.max(2, Number(e.target.value) || 262655))}
                  />
                </label>
              </div>

              <label className="mt-3 block">
                <span className="mb-1 block text-[11px] text-slate-500">
                  Boshlanish ko'chasi (ixtiyoriy)
                </span>
                <input
                  type="text"
                  className="input"
                  value={batchStartStreet}
                  onChange={(e) => setBatchStartStreet(e.target.value)}
                  placeholder="Masalan: Навбаҳор. Bo'sh — birinchi ko'cha"
                />
                <span className="mt-1 block text-[11px] text-slate-500">
                  Ko'cha nomini yoki uning bir qismini kiriting. Bo'sh
                  qoldirilsa, avvalgi holat bo'lmasa ro'yxatdagi birinchi
                  ko'cha ochiladi.
                </span>
              </label>

              <label className="mt-3 flex items-start gap-2 text-[11px] text-slate-400">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={reuseSession}
                  onChange={(e) => setReuseSession(e.target.checked)}
                />
                <span>
                  Saqlangan sessiyadan foydalanish — birinchi muvaffaqiyatli
                  login'dan keyin keyingi runlar parolsiz boshlanadi. Sessiya
                  eskirsa yoki sayt har bir odamdan keyin chiqarib yuborsa,
                  LOGIN/PASSWORD bilan avtomatik qayta login qilinadi (shuning
                  uchun ularni kiritib qo'ying).
                </span>
              </label>

              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
                <span>
                  Jami:{' '}
                  <span className="text-amber-300">
                    {batchHouseholds > 0
                      ? `${batchHouseholds * batchPeople} ta odam`
                      : 'cheksiz (ko‘chalar tugaguncha)'}
                  </span>
                </span>
                <span>Telefon: har bir odamga alohida</span>
                <span>Excel #{batchStartRow}dan boshlanadi</span>
                <span>
                  Ko'cha: {batchStartStreet.trim() || 'birinchi / davom etish'}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Bosqichlar */}
      <div className="mb-6 rounded-xl border border-slate-800 bg-slate-900/60">
        <div className="border-b border-slate-800 px-4 py-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-300">{isRecorded ? 'Shablon qadamlari' : 'Aniqlangan bosqichlar'}</h3>
          <span className="text-xs text-slate-500">{steps.length} ta</span>
        </div>
        <div className="divide-y divide-slate-800">
          {steps.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-slate-500">Bosqichlar topilmadi.</div>
          ) : (
            steps.map((step) => (
              <div key={step.id} className="px-4 py-3 flex items-center gap-3 hover:bg-slate-800/30 transition">
                <div className="flex h-6 w-6 items-center justify-center rounded-full border border-slate-600 text-[10px] font-mono text-slate-400 shrink-0">
                  {step.order_index + 1}
                </div>
                {editing === step.id ? (
                  <StepEditor step={step} value={stepEdit} onChange={setStepEdit} onSave={() => saveStep(step)} onCancel={() => setEditing(null)} />
                ) : (
                  <>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 text-xs font-mono text-slate-400 mb-0.5">
                        <span className={`bg-slate-800 px-1.5 rounded ${{
                          navigate: 'text-sky-400 border-sky-500/30',
                          click: 'text-emerald-400 border-emerald-500/30',
                          type: 'text-amber-400 border-amber-500/30',
                          select: 'text-violet-400 border-violet-500/30',
                          check: 'text-violet-400 border-violet-500/30',
                          press: 'text-cyan-400 border-cyan-500/30',
                          upload: 'text-rose-400 border-rose-500/30',
                          submit: 'text-emerald-400 border-emerald-500/30',
                          wait: 'text-slate-400 border-slate-600',
                        }[step.action] ?? 'text-slate-400 border-slate-600'}`}>{step.action}</span>
                        {step.input_required && <span className="bg-amber-500/10 text-amber-400 px-1.5 rounded text-[10px]">input kerak</span>}
                      </div>
                      <div className="text-sm text-slate-200 truncate">{step.description}</div>
                      {step.selector && (
                        <div className="text-[10px] text-slate-500 truncate max-w-[400px]">{step.selector}</div>
                      )}
                    </div>
                    <div className="flex items-center gap-1">
                      {step.screenshot_path && (
                        <button
                          onClick={(e) => { e.stopPropagation(); window.open(api.getScreenshot(id, step.order_index), '_blank') }}
                          title="Screenshot"
                          className="btn-ghost p-1.5 hover:text-sky-400"
                        >
                          <ImageIcon size={14} />
                        </button>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); setEditing(step.id); setStepEdit({ action: step.action, selector: step.selector, value_template: step.value_template, value_hint: step.value_hint, description: step.description, input_required: step.input_required, input_key: step.input_key }) }}
                        title="Tahrirlash"
                        className="btn-ghost p-1.5 hover:text-emerald-400"
                      >
                        <Edit2 size={14} />
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); copyText(step.selector ?? '').catch(() => {}) }}
                        title="Nusxalash"
                        className="btn-ghost p-1.5 hover:text-sky-400"
                      >
                        <Copy size={14} />
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Loglar */}
      <div className="rounded-xl border border-slate-800 bg-black/40 p-3">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-[0.15em]">Real vaqt loglari</h3>
          <span className="text-[10px] text-slate-500">{allLogs.length} ta yozuv</span>
        </div>
        <div ref={scrollRef} className="h-64 overflow-y-auto font-mono text-xs space-y-1">
          {allLogs.map((l, i) => <div key={i}>{l}</div>)}
          {!allLogs.length && <div className="text-slate-600">Loglar shu yerda ko‘rinadi…</div>}
        </div>
      </div>

      {/* Xatolik bilan qayta tahlil */}
      {!isRecorded && status === 'failed' && errorStep !== undefined && (
        <div className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3">
          <div className="flex items-start gap-2 mb-1">
            <AlertTriangle size={16} className="text-red-400" />
            <span className="text-sm text-red-300 font-medium">Xato bosqich #{errorStep + 1}</span>
          </div>
          <button onClick={() => api.reanalyze(id).then(() => fetchJob())} className="text-xs text-emerald-400 hover:underline mt-1">
            Qayta tahlil qiling
          </button>
        </div>
      )}
    </div>
  )
}

function StepEditor({ step, value, onChange, onSave, onCancel }: {
  step: Step; value: Partial<Step>; onChange: (v: Partial<Step>) => void; onSave: () => void; onCancel: () => void
}) {
  return (
    <div className="bg-slate-800/50 border border-slate-700 rounded p-3 space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <input className="input text-sm" value={value.action ?? step.action} onChange={(e) => onChange({ ...value, action: e.target.value })} />
        <input className="input text-sm" value={value.selector ?? step.selector ?? ''} onChange={(e) => onChange({ ...value, selector: e.target.value })} />
      </div>
      <input className="input text-sm" value={value.description ?? step.description} onChange={(e) => onChange({ ...value, description: e.target.value })} />
      <div className="flex items-center justify-between">
        <button onClick={onSave} className="text-xs btn-primary px-2 py-1.5"><Save size={12} className="mr-1" /> Saqlash</button>
        <button onClick={onCancel} className="text-xs btn-ghost px-2 py-1.5"><X size={12} /> Bekor</button>
      </div>
    </div>
  )
}
