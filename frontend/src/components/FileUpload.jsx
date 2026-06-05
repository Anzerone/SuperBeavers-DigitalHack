import { useState, useRef } from 'react'
import { uploadFile, startProcessing } from '../api.js'

export default function FileUpload({ runId, setRunId }) {
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploaded, setUploaded] = useState(null)
  const inputRef = useRef()

  const handleUpload = async (f) => {
    setUploading(true)
    try {
      const result = await uploadFile(f)
      setUploaded(result)
      setFile(f)
      // Auto-start processing
      const proc = await startProcessing(result.filepath, result.filename)
      setRunId(proc.run_id)
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

  return (
    <div className="bg-white rounded-xl p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-sm text-gray-700">Загрузка данных</h3>
        {uploaded && (
          <span className="text-xs px-2 py-0.5 bg-green-100 text-green-700 rounded-full">
            Готово
          </span>
        )}
      </div>

      {!uploaded ? (
        <div
          onDrop={handleDrop}
          onDragOver={(e) => e.preventDefault()}
          onClick={() => inputRef.current?.click()}
          className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center cursor-pointer hover:border-[#0d7377] transition"
        >
          <div className="text-3xl mb-2">📄</div>
          <p className="text-sm font-medium text-gray-700">Excel с обращениями</p>
          <p className="text-xs text-gray-400">.xlsx, до 400 000 строк</p>
          {uploading && <p className="text-xs text-[#0d7377] mt-2">Загрузка...</p>}
        </div>
      ) : (
        <div className="bg-gray-50 rounded-lg p-3">
          <div className="flex items-center gap-2">
            <span className="text-lg">📎</span>
            <div className="min-w-0">
              <p className="text-sm font-medium truncate">{uploaded.filename}</p>
              <p className="text-xs text-gray-400">{uploaded.size_mb} МБ</p>
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
