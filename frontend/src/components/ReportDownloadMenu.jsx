import { useRef, useState } from 'react'
import { downloadReport } from '../api.js'
import GovIcon from './GovIcon.jsx'

export default function ReportDownloadMenu({ runId }) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState('')
  const closeTimer = useRef(null)

  const download = async (format) => {
    if (!runId) return
    setLoading(format)
    try {
      await downloadReport(runId, format)
      setOpen(false)
    } finally {
      setLoading('')
    }
  }

  return (
    <div
      className="relative"
      onMouseLeave={() => { closeTimer.current = setTimeout(() => setOpen(false), 200) }}
      onMouseEnter={() => { if (closeTimer.current) clearTimeout(closeTimer.current) }}
    >
      <button
        onClick={() => setOpen(v => !v)}
        disabled={!runId}
        className="inline-flex items-center gap-2 rounded-lg bg-[#2B3990] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#1F2A6E] disabled:opacity-40"
      >
        <GovIcon name="report" className="h-4 w-4" />
        Отчет
        <GovIcon name={open ? 'chevronUp' : 'chevronDown'} className="h-4 w-4" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-2 w-56 overflow-hidden rounded-lg border border-gray-100 bg-white shadow-lg">
          <button
            onClick={() => download('excel')}
            className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm text-gray-700 hover:bg-gray-50"
          >
            <GovIcon name="download" className="h-4 w-4 text-emerald-600" />
            <span className="flex-1">
              Excel
              <span className="block text-xs text-gray-400">таблицы и листы отчета</span>
            </span>
            {loading === 'excel' && <GovIcon name="spinner" className="h-4 w-4 animate-spin text-gray-400" />}
          </button>
          <button
            onClick={() => download('docx')}
            className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm text-gray-700 hover:bg-gray-50"
          >
            <GovIcon name="file" className="h-4 w-4 text-indigo-600" />
            <span className="flex-1">
              Word
              <span className="block text-xs text-gray-400">аналитическая записка</span>
            </span>
            {loading === 'docx' && <GovIcon name="spinner" className="h-4 w-4 animate-spin text-gray-400" />}
          </button>
        </div>
      )}
    </div>
  )
}
