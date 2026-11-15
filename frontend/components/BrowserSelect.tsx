'use client'

import { Globe } from 'lucide-react'
import { BrowserType, browserOptions } from '@/lib/api'

export default function BrowserSelect({
  value,
  onChange,
  disabled = false,
}: {
  value: BrowserType
  onChange: (browser: BrowserType) => void
  disabled?: boolean
}) {
  const selected = browserOptions.find((option) => option.value === value) ?? browserOptions[0]

  return (
    <div>
      <label className="block">
        <span className="mb-1 block text-xs font-medium text-slate-400">
          Avtomatizatsiya brauzeri
        </span>
        <span className="relative block">
          <Globe
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500"
          />
          <select
            className="input pl-10"
            value={value}
            disabled={disabled}
            onChange={(event) => onChange(event.target.value as BrowserType)}
          >
            {browserOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </span>
      </label>
      <p className="mt-1 text-[11px] text-slate-500">{selected.description}</p>
    </div>
  )
}
