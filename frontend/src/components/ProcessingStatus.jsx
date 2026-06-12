import { useEffect, useState } from 'react'
import { cancelProcessing } from '../api.js'
import GovIcon from './GovIcon.jsx'

const STEPS = [
  { name: 'Анализ содержания писем', startAt: 0.03, doneAt: 0.4 },
  { name: 'Категоризация и уточнение сложных случаев', startAt: 0.4, doneAt: 0.62 },
  { name: 'Поиск повторяющихся проблем по районам', startAt: 0.7, doneAt: 0.86 },
  { name: 'Подготовка аналитической сводки', startAt: 0.9, doneAt: 1 },
]

const TICK_MS = 80
const CAP_PADDING = 0.008

function clampProgress(value) {
  return Math.max(0, Math.min(value || 0, 1))
}

function visualCapFor(progress) {
  if (progress >= 1 - CAP_PADDING) return 1

  const currentStep = STEPS.find(step => progress >= step.startAt && progress < step.doneAt)
  if (currentStep) return Math.max(progress, currentStep.doneAt - CAP_PADDING)

  const nextStep = STEPS.find(step => progress < step.startAt)
  if (nextStep) return Math.max(progress, nextStep.startAt - CAP_PADDING)

  return Math.max(progress, 1 - CAP_PADDING)
}

// Статус приходит сверху (App опрашивает бэкенд глобально), компонент только
// анимирует прогресс — обработка продолжается и при уходе на другую страницу.
export default function ProcessingStatus({ runId, status }) {
  const [displayProgress, setDisplayProgress] = useState(0)
  const [cancelling, setCancelling] = useState(false)

  useEffect(() => {
    setDisplayProgress(0)
    setCancelling(false)
  }, [runId])

  const handleCancel = async () => {
    if (!runId || cancelling) return
    const message = status?.status === 'pending'
      ? 'Убрать файл из очереди обработки?'
      : 'Прервать обработку файла? Прогресс будет потерян.'
    if (!window.confirm(message)) return
    setCancelling(true)
    try {
      await cancelProcessing(runId)
    } catch (e) {
      alert('Не удалось остановить: ' + (e.response?.data?.detail || e.message))
      setCancelling(false)
    }
  }

  useEffect(() => {
    if (!status) return
    const target = clampProgress(status.status === 'completed' ? 1 : status.progress)
    const cap = status.status === 'running' ? visualCapFor(target) : target
    const timer = setInterval(() => {
      setDisplayProgress((prev) => {
        if (target < prev && status.status !== 'running') return target
        const diff = target - prev

        if (diff > 0.001) {
          return Math.min(target, prev + Math.max(diff * 0.24, 0.002))
        }

        if (status.status !== 'running') {
          if (Math.abs(diff) < 0.001) {
            clearInterval(timer)
            return target
          }
          return prev + diff * 0.24
        }

        const remaining = cap - prev
        if (remaining <= 0.0005) {
          clearInterval(timer)
          return Math.max(prev, target)
        }

        return Math.min(cap, prev + Math.max(remaining * 0.004, 0.00002))
      })
    }, TICK_MS)

    return () => clearInterval(timer)
  }, [status])

  if (!runId || !status) return null

  // Файл ждёт своей очереди: показываем позицию и что обрабатывается сейчас.
  if (status.status === 'pending') {
    return (
      <div className="bg-white rounded-lg p-4 shadow-sm">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-semibold text-gray-800">Очередь обработки</span>
          <span className="flex items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-semibold text-amber-700">
            <GovIcon name="clock" className="h-3.5 w-3.5" />
            {status.queue_position ? `${status.queue_position}-й в очереди` : 'В очереди'}
          </span>
        </div>

        <p className="text-xs leading-5 text-gray-500">
          Файл <span className="font-medium text-gray-700">{status.filename}</span> поставлен в очередь
          {status.queue_size > 1 && status.queue_position
            ? ` (позиция ${status.queue_position} из ${status.queue_size})`
            : ''}.
          Обработка начнётся автоматически.
        </p>

        {status.active_run && (
          <div className="mt-3 rounded-lg bg-gray-50 px-3 py-2">
            <p className="text-[11px] uppercase tracking-wide text-gray-400">Сейчас обрабатывается</p>
            <p className="mt-0.5 truncate text-xs font-medium text-gray-700" title={status.active_run.filename}>
              {status.active_run.filename}
            </p>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-gray-200">
              <div
                className="h-full rounded-full bg-[#2B3990] transition-[width] duration-500"
                style={{ width: `${Math.round((status.active_run.progress || 0) * 100)}%` }}
              />
            </div>
            <p className="mt-1 text-right text-[11px] text-gray-400">
              {Math.round((status.active_run.progress || 0) * 100)}%
            </p>
          </div>
        )}

        <button
          onClick={handleCancel}
          disabled={cancelling}
          className="mt-3 inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 transition hover:bg-red-50 disabled:opacity-50"
        >
          <GovIcon name="close" className="h-3.5 w-3.5" />
          {cancelling ? 'Убираем…' : 'Убрать из очереди'}
        </button>
      </div>
    )
  }

  const displayPct = Math.max(0, Math.min(displayProgress * 100, 100))
  const pct = Math.round(displayPct)
  const isDone = status.status === 'completed'
  const isFailed = status.status === 'failed'

  const stepStates = STEPS.map((step) => {
    if (status.progress >= step.doneAt || isDone) return 'done'
    if (status.progress >= step.startAt) return 'active'
    return 'pending'
  })

  return (
    <div className="bg-white rounded-lg p-4 shadow-sm">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-gray-800">Обработка</span>
        <span className="text-sm font-semibold text-[#2B3990]">{pct}%</span>
      </div>

      <div className="h-2 bg-gray-200 rounded-full mb-3 overflow-hidden">
        <div
          className={`h-full rounded-full transition-[width] duration-300 ease-out ${
            isFailed ? 'bg-red-500' : isDone ? 'bg-green-500' : 'bg-[#2B3990]'
          }`}
          style={{ width: `${displayPct}%` }}
        />
      </div>

      {status.current_step && (
        <p className="mb-3 text-xs leading-5 text-gray-500">{status.current_step}</p>
      )}

      <div className="space-y-2">
        {STEPS.map((step, index) => {
          const state = stepStates[index]
          return (
            <div key={step.name} className="flex items-center gap-2 text-sm">
              {state === 'done' && <GovIcon name="check" className="h-4 w-4 shrink-0 text-green-500" />}
              {state === 'active' && <GovIcon name="spinner" className="h-4 w-4 shrink-0 animate-spin text-[#2B3990]" />}
              {state === 'pending' && <GovIcon name="clock" className="h-4 w-4 shrink-0 text-gray-300" />}
              <span
                className={
                  state === 'done'
                    ? 'text-gray-500'
                    : state === 'active'
                      ? 'font-medium text-gray-800'
                      : 'text-gray-400'
                }
              >
                {step.name}
              </span>
            </div>
          )
        })}
      </div>

      {status.status === 'running' && (
        <button
          onClick={handleCancel}
          disabled={cancelling}
          className="mt-3 inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 transition hover:bg-red-50 disabled:opacity-50"
        >
          <GovIcon name="stop" className="h-3.5 w-3.5" />
          {cancelling ? 'Останавливаем…' : 'Прервать обработку'}
        </button>
      )}

      {isFailed && (
        <p className="mt-3 text-xs text-red-500">Ошибка: {status.error_message?.slice(0, 160)}</p>
      )}
    </div>
  )
}
