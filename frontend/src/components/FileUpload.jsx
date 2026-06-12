import { useEffect, useRef, useState } from 'react'
import { getStatus, startProcessing, uploadFile } from '../api.js'
import GovIcon from './GovIcon.jsx'

const STATUS_LABELS = {
  completed: 'Готово',
  running: 'Обработка',
  failed: 'Ошибка',
  pending: 'В очереди',
}

const STATUS_BADGES = {
  completed: 'bg-green-100 text-green-700',
  running: 'bg-blue-50 text-blue-700',
  failed: 'bg-red-50 text-red-600',
  pending: 'bg-amber-50 text-amber-700',
}

function restoredUpload(status) {
  if (!status?.filename) return null
  return {
    filename: status.filename,
    size_mb: null,
    status: status.status,
    restored: true,
  }
}

export default function FileUpload({ runId, setRunId }) {
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploaded, setUploaded] = useState(null)
  const inputRef = useRef()

  useEffect(() => {
    if (!runId) {
      setUploaded(null)
      setFile(null)
      return
    }

    let cancelled = false
    getStatus(runId)
      .then(status => {
        if (cancelled) return
        const restored = restoredUpload(status)
        if (restored) {
          setUploaded(prev => (
            prev?.filename === restored.filename
              ? { ...prev, status: restored.status }
              : restored
          ))
        }
      })
      .catch(() => {
        if (!cancelled) setUploaded(null)
      })

    return () => {
      cancelled = true
    }
  }, [runId])

  const handleUpload = async (f) => {
    setUploading(true)
    try {
      const result = await uploadFile(f)
      setUploaded({ ...result, status: 'running' })
      setFile(f)
      const proc = await startProcessing(result.filepath, result.filename)
      setUploaded(prev => ({ ...prev, status: proc.status || 'running' }))
      setRunId(proc.run_id)
      if (proc.status === 'pending' && proc.queue_position) {
        alert(`Сейчас обрабатывается другой файл. Ваш файл поставлен в очередь: позиция ${proc.queue_position}. Обработка начнётся автоматически.`)
      }
    } catch (e) {
      alert('Ошибка загрузки: ' + (e.response?.data?.detail || e.message))
    } finally {
      setUploading(false)
    }
  }

  const handleDrop = (e) => {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f) handleUpload(f)
  }

  const statusLabel = uploaded ? STATUS_LABELS[uploaded.status] || 'Загружено' : ''
  const statusBadge = uploaded ? STATUS_BADGES[uploaded.status] || 'bg-green-100 text-green-700' : ''

  return (
    <div className="bg-white rounded-xl p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-sm text-gray-700">Загрузка данных</h3>
        {uploaded && (
          <span className={`text-xs px-2 py-0.5 rounded-full ${statusBadge}`}>
            {statusLabel}
          </span>
        )}
      </div>

      {!uploaded ? (
        <div
          onDrop={handleDrop}
          onDragOver={(e) => e.preventDefault()}
          onClick={() => inputRef.current?.click()}
          className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center cursor-pointer hover:border-[#2B3990] transition"
        >
          <GovIcon name="file" className="mx-auto mb-2 h-8 w-8 text-[#2B3990]" />
          <p className="text-sm font-medium text-gray-700">Excel с обращениями</p>
          <p className="text-xs text-gray-400">.xlsx, .xls</p>
          {uploading && <p className="text-xs text-[#2B3990] mt-2">Загрузка...</p>}
        </div>
      ) : (
        <div className="bg-gray-50 rounded-lg p-3">
          <div className="flex items-center gap-2">
            <GovIcon name="attachment" className="h-5 w-5 shrink-0 text-[#2B3990]" />
            <div className="min-w-0">
              <p className="text-sm font-medium truncate">{uploaded.filename}</p>
              <p className="text-xs text-gray-400">
                {uploaded.size_mb != null ? `${uploaded.size_mb} МБ` : 'Текущая загрузка восстановлена'}
              </p>
            </div>
          </div>
        </div>
      )}

      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls"
        className="hidden"
        onChange={(e) => e.target.files[0] && handleUpload(e.target.files[0])}
      />

      {uploaded && (
        <button
          onClick={() => { setUploaded(null); setFile(null); setRunId(null) }}
          className="mt-3 w-full py-2 border border-gray-300 rounded-lg text-sm text-gray-600 hover:bg-gray-50"
        >
          Заменить файл
        </button>
      )}
    </div>
  )
}
