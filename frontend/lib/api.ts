"use client"

const BASE = (process.env.NEXT_PUBLIC_API_URL || "/api").replace(/\/+$/, "")
const TOKEN_KEY = "vw_token"
const API_KEY = process.env.NEXT_PUBLIC_API_KEY?.trim()

function authHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra)
  if (API_KEY && !headers.has("x-api-key")) headers.set("x-api-key", API_KEY)
  if (typeof window !== "undefined" && !headers.has("Authorization")) {
    const token = localStorage.getItem(TOKEN_KEY)
    if (token) headers.set("Authorization", `Bearer ${token}`)
  }
  return headers
}

function tokenQuery(): string {
  if (typeof window === "undefined") return ""
  const token = localStorage.getItem(TOKEN_KEY)
  return token ? `?token=${encodeURIComponent(token)}` : ""
}

type JobStatus = "pending" | "analyzing" | "analyzed" | "awaiting_input" | "running" | "completed" | "failed"

export type BrowserType = "chromium" | "chrome" | "msedge" | "firefox" | "webkit"

export const browserOptions: Array<{
  value: BrowserType
  label: string
  description: string
}> = [
  { value: "chromium", label: "Chromium", description: "Playwright Chromium — ko‘p Linux/Windows saytlari uchun standart variant." },
  { value: "chrome", label: "Google Chrome", description: "Kompyuterda o‘rnatilgan Google Chrome ishlatiladi." },
  { value: "msedge", label: "Microsoft Edge", description: "Kompyuterda o‘rnatilgan Microsoft Edge ishlatiladi." },
  { value: "firefox", label: "Firefox", description: "Playwright Firefox brauzeri ishlatiladi." },
  { value: "webkit", label: "WebKit (Safari yadrosi)", description: "Safari’ga yaqin WebKit engine; Safari ilovasining o‘zi emas." },
]

export interface Step {
  id: string
  job_id: string
  order_index: number
  action: string
  selector: string | null
  element_type: string | null
  value_template: string | null
  value_hint: string | null
  description: string
  confidence: number
  input_required: boolean
  input_key: string | null
  meta: Record<string, any>
  screenshot_path: string | null
}

export interface Job {
  id: string
  filename: string
  status: JobStatus
  source?: "video" | "recorded"
  error: string | null
  storage_path: string | null
  content_type?: string | null
  size_bytes: number | null
  duration_sec: number | null
  created_at: string | null
  updated_at: string | null
  steps: Step[]
}

export interface RecordedEvent {
  seq: number
  action: string
  selector: string | null
  element_type: string | null
  value_template: string | null
  value_hint: string | null
  description: string
  input_required: boolean
  input_key: string | null
  meta: Record<string, any>
  ts: string
  has_screenshot: boolean
}

export interface RecordingSession {
  id: string
  status: "starting" | "recording" | "stopped" | "closed" | "error" | string
  start_url: string
  current_url: string
  browser: BrowserType
  error: string | null
  events: RecordedEvent[]
  event_count: number
  created_at: number
  active?: boolean
}

export interface InputRequirement {
  key: string
  hint: string | null
  required: boolean
  example: string | null
}

export interface InputRequirements {
  job_id: string
  fields: InputRequirement[]
}

export interface Run {
  id: string
  job_id: string
  status: JobStatus
  inputs: Record<string, string>
  browser: BrowserType
  current_step: number
  total_steps: number
  error: string | null
  error_step: number | null
  created_at: string | null
  finished_at: string | null
}

export interface ExcelPerson {
  row_num: number
  pinpp: string
  surname: string
  name: string
  patronymic: string
  full_name: string
  birth_date: string
  passport: string
  address: string
  district?: string
}

export interface ExcelDataset {
  id: string
  name: string
  rows: number
}

export interface ExcelUploadResult extends ExcelDataset {
  columns?: string[]
  skipped?: number
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

async function errorFrom(res: Response, fallback: string): Promise<ApiError> {
  const txt = await res.text().catch(() => "")
  let detail = txt
  try {
    const parsed = JSON.parse(txt)
    detail = parsed?.detail || parsed?.message || txt
  } catch {
    // matn javobi — o'zidek qoldiriladi
  }
  return new ApiError(detail || `${fallback} ${res.status}`, res.status)
}

async function request(path: string, opts?: RequestInit) {
  const headers = authHeaders(opts?.headers)
  if (opts?.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers,
  })
  if (!res.ok) {
    throw await errorFrom(res, "Request failed")
  }
  if (res.status === 204) return null
  try {
    return await res.json()
  } catch {
    return null
  }
}

export async function login(body: { email: string; password: string }) {
  const res = await fetch(`${BASE}/login`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    throw await errorFrom(res, 'Login failed')
  }
  return res.json()
}

export async function register(body: { email: string; password: string }) {
  const res = await fetch(`${BASE}/register`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    throw await errorFrom(res, 'Register failed')
  }
  return res.json()
}

export const api = {
  listJobs: (params?: { status?: string; page?: number; page_size?: number }) => {
    const qs = new URLSearchParams()
    if (params?.status) qs.set("status", params.status)
    if (params?.page) qs.set("page", String(params.page))
    if (params?.page_size) qs.set("page_size", String(params.page_size))
    return request(`/jobs?${qs.toString()}`)
  },
  getJob: (id: string) => request(`/jobs/${id}`),
  uploadVideo: (file: File) => {
    const fd = new FormData()
    fd.append("file", file)
    return fetch(`${BASE}/jobs`, { method: "POST", headers: authHeaders(), body: fd }).then(async (res) => {
      if (!res.ok) {
        throw await errorFrom(res, "Upload failed")
      }
      return res.json()
    })
  },
  deleteJob: (id: string) => request(`/jobs/${id}`, { method: "DELETE" }),
  getVideo: (id: string) => `${BASE}/jobs/${id}/video${tokenQuery()}`,
  getScreenshot: (jobId: string, order: number) => `${BASE}/jobs/${jobId}/screenshot/${order}${tokenQuery()}`,
  listSteps: (jobId: string) => request(`/jobs/${jobId}/steps`),
  inputRequirements: (jobId: string) => request(`/jobs/${jobId}/input-requirements`),
  updateStep: (stepId: string, body: any) => request(`/steps/${stepId}`, { method: "PATCH", body: JSON.stringify(body) }),
  reanalyze: (jobId: string) => request(`/jobs/${jobId}/reanalyze`, { method: "POST" }),
  startRun: (jobId: string, inputs: Record<string, string>, browser?: BrowserType) =>
    request(`/jobs/${jobId}/run`, {
      method: "POST",
      body: JSON.stringify({ inputs, ...(browser ? { browser } : {}) }),
    }),
  listRuns: (jobId: string) => request(`/jobs/${jobId}/runs`),
  getRun: (runId: string) => request(`/runs/${runId}`),
  cancelRun: (runId: string) => request(`/runs/${runId}/cancel`, { method: "POST" }),
  uploadExcel: (file: File): Promise<ExcelUploadResult> => {
    const fd = new FormData()
    fd.append("file", file)
    return fetch(`${BASE}/excel/upload`, { method: "POST", headers: authHeaders(), body: fd }).then(async (res) => {
      if (!res.ok) {
        throw await errorFrom(res, "Excel upload failed")
      }
      return res.json()
    })
  },
  excelDatasets: (): Promise<{ items: ExcelDataset[] }> => request(`/excel/datasets`),
  excelSearch: (dataset: string, q: string): Promise<{ items: ExcelPerson[] }> =>
    request(`/excel/search?dataset=${encodeURIComponent(dataset)}&q=${encodeURIComponent(q)}`),
  startRecording: (url: string, browser?: BrowserType): Promise<RecordingSession> =>
    request(`/recorder/start`, {
      method: "POST",
      body: JSON.stringify({ url, ...(browser ? { browser } : {}) }),
    }),
  activeRecording: (): Promise<{ active: boolean; session: RecordingSession | null }> =>
    request(`/recorder/active`),
  getRecording: (sessionId: string): Promise<RecordingSession> =>
    request(`/recorder/${sessionId}`),
  stopRecording: (sessionId: string, name?: string): Promise<Job> =>
    request(`/recorder/${sessionId}/stop`, {
      method: "POST",
      body: JSON.stringify(name ? { name } : {}),
    }),
  cancelRecording: (sessionId: string) =>
    request(`/recorder/${sessionId}`, { method: "DELETE" }),
}
