import { useEffect, useState } from 'react'
import { getStatus } from '../api.js'
import GovIcon from './GovIcon.jsx'

const STEPS = [
  { name: 'Анализ содержания писем', startAt: 0.03, doneAt: 0.4 },
  { name: 'Категоризация и уточнение сложных случаев', startAt: 0.4, doneAt: 0.62 },
  { name: 'Поиск повторяющихся проблем по районам', startAt: 0.7, doneAt: 0.86 },
  { name: 'Подготовка аналитической сводки', startAt: 0.9, doneAt: 1 },
]

export default function ProcessingStatus({ runId, onComplete }) {
  const [status, setStatus] = useState(null)
  const [displayProgress, setDisplayProgress] = useState(0)

  useEffect(() => {
    setStatus(null)
    setDisplayProgress(0)
  }, [runId])

  useEffect(() => {
    if (!runId) return
    let cancelled = false

    const poll = async () => {
      try {
        const nextStatus = await getStatus(runId)
        if (cancelled) return
        setStatus(nextStatus)
        if (nextStatus.status === 'completed') {
          onComplete?.()
        } else if (nextStatus.status === 'running') {
          setTimeout(poll, 2000)
        }
      } catch (e) {
        if (!cancelled) setTimeout(poll, 3000)
      }
    }

    poll()
    return () => {
      cancelled = true
    }
  }, [runId, onComplete])

  useEffect(() => {
    if (!status) return
    const target = Math.max(0, Math.min(status.progress || 0, 1))
    const timer = setInterval(() => {
      setDisplayProgress((prev) => {
        if (target < prev) return target
        const diff = target - prev
        if (Math.abs(diff) < 0.001) {
          clearInterval(timer)
          return target
        }
        return prev + diff * 0.22
      })
    }, 80)

    return () => clearInterval(timer)
  }, [status])

  if (!runId || !status) return null

  const pct = Math.round(displayProgress * 100)
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
        <span className="text-sm font-semibold text-[#0d7377]">{pct}%</span>
      </div>

      <div className="h-2 bg-gray-200 rounded-full mb-3 overflow-hidden">
        <div
          className={`h-full rounded-full transition-[width] duration-300 ease-out ${
            isFailed ? 'bg-red-500' : isDone ? 'bg-green-500' : 'bg-[#0d7377]'
          }`}
          style={{ width: `${pct}%` }}
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
              {state === 'active' && <GovIcon name="spinner" className="h-4 w-4 shrink-0 animate-spin text-[#0d7377]" />}
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

      {isFailed && (
        <p className="mt-3 text-xs text-red-500">Ошибка: {status.error_message?.slice(0, 160)}</p>
      )}
    </div>
  )
}
