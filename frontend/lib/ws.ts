"use client"

import { useEffect, useRef, useState, useCallback } from "react"
import { useAuth } from "@/lib/auth"

const MAX_LOGS = 1000

function normalizeHost(url: URL) {
  // Windows'da `localhost` ba'zan IPv6 (::1) ga yechiladi, backend esa
  // 127.0.0.1 da tinglaydi.
  if (url.hostname === "localhost" && (url.port === "8000" || url.port === "")) {
    url.hostname = "127.0.0.1"
  }
  return url
}

function getWebSocketBase() {
  const configured = process.env.NEXT_PUBLIC_WS?.trim()
  if (configured) return configured.replace(/\/+$/, "")

  const apiBase = process.env.NEXT_PUBLIC_API_URL?.trim()
  if (apiBase && /^https?:\/\//i.test(apiBase)) {
    const url = new URL(apiBase)
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:"
    url.pathname = `${url.pathname.replace(/\/+$/, "")}/ws`
    url.search = ""
    url.hash = ""
    return normalizeHost(url).toString()
  }

  // UI qaysi hostda ochilgan bo'lsa, backend ham shu hostdan olinadi.
  // HTTPS reverse proxy uchun NEXT_PUBLIC_WS ni alohida ko'rsatish mumkin.
  const url = new URL(window.location.origin)
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
  if (url.hostname === "localhost") url.hostname = "127.0.0.1"
  url.port = "8000"
  url.pathname = "/api/ws"
  url.search = ""
  url.hash = ""
  return url.toString()
}

export function useJobWS(jobId: string) {
  const [logs, setLogs] = useState<string[]>([])
  const [status, setStatus] = useState<string>("idle")
  const [currentStep, setCurrentStep] = useState<number | undefined>()
  const [errorStep, setErrorStep] = useState<number | undefined>()
  // Har bir job/step hodisasida oshadi — sahifa ma'lumotni qayta yuklaydi.
  const [revision, setRevision] = useState(0)
  const { token } = useAuth()

  useEffect(() => {
    if (!jobId) return
    let closed = false
    let socket: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let attempts = 0

    const appendLog = (line: string) => {
      setLogs((prev) => [...prev, line].slice(-MAX_LOGS))
    }

    const connect = () => {
      if (closed) return
      const tokenQuery = token ? `?token=${encodeURIComponent(token)}` : ""
      const wsUrl = getWebSocketBase() + `/jobs/${jobId}${tokenQuery}`
      const ws = new WebSocket(wsUrl)
      socket = ws

      ws.onopen = () => { attempts = 0 }

      ws.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data)
          if (m.type === "ping") return
          if (m.type === "log") {
            appendLog(`[${m.ts || "--:--:--"}] ${m.message || ""}`)
          }
          if (m.type === "status") {
            // Analyzer holati: analyzing / analyzed / failed
            setRevision((r) => r + 1)
            if (m.status === "failed" && m.error) appendLog(`❌ ${m.error}`)
          }
          if (m.type === "step") {
            setRevision((r) => r + 1)
          }
          if (m.type === "run_status") {
            setStatus((current) => m.status || current)
            if (typeof m.step === "number") setCurrentStep(m.step)
            if (m.error_step !== undefined) setErrorStep(m.error_step)
            if (m.error) appendLog(`❌ ${m.error}`)
            if (m.status === "completed") appendLog("✅ Bajarish yakunlandi")
            if (m.status === "cancelled") appendLog("⛔ Bekor qilindi")
          }
        } catch {
          // ignore malformed
        }
      }

      ws.onerror = () => appendLog("⚠️ WS xatosi")

      ws.onclose = () => {
        if (closed) return
        attempts += 1
        const delay = Math.min(1000 * 2 ** Math.min(attempts, 4), 15000)
        retryTimer = setTimeout(connect, delay)
      }
    }

    connect()
    return () => {
      closed = true
      if (retryTimer) clearTimeout(retryTimer)
      socket?.close()
    }
  }, [jobId, token])

  const scrollRef = useCallback((el: HTMLDivElement | null) => {
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  return { logs, status, currentStep, errorStep, revision, scrollRef }
}
