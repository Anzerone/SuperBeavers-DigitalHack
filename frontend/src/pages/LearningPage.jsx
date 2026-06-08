import { useEffect, useState } from 'react'
import { annotateAppeal, getLearningQueue, getLearningStats } from '../api.js'
import { formatSeverity } from '../utils/severity.js'
import GovIcon from '../components/GovIcon.jsx'

export default function LearningPage({ runId }) {
  const [queue, setQueue] = useState([])
  const [meta, setMeta] = useState({ categories: [], severities: [] })
  const [annotatedCount, setAnnotatedCount] = useState(0)
  const [idx, setIdx] = useState(0)
  const [loading, setLoading] = useState(false)
  const [stats, setStats] = useState(null)

  const loadQueue = async () => {
    if (!runId) return
    setLoading(true)
    try {
      const data = await getLearningQueue(runId, 15)
      setQueue(data.queue || [])
      setMeta({ categories: data.categories || [], severities: data.severities || [] })
      setAnnotatedCount(data.annotated_count || 0)
      setIdx(0)
      setStats(await getLearningStats())
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadQueue() }, [runId])

  const current = queue[idx]

  const submit = async (payload) => {
    if (!current) return
    await annotateAppeal({ appeal_id: current.appeal_id, ...payload })
    setAnnotatedCount(c => c + 1)
    if (idx + 1 >= queue.length) {
      await loadQueue()
    } else {
      setIdx(i => i + 1)
    }
  }

  if (!runId) {
    return (
      <div className="flex h-[80vh] items-center justify-center">
        <p className="text-gray-400">Сначала запустите обработку на дашборде</p>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl p-4">
      <div className="bg-white rounded-xl shadow-sm p-5 mb-4">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-gray-800">
          <GovIcon name="feedback" className="h-5 w-5 text-[#0d7377]" />
          Обратная связь
        </h2>
        <p className="text-sm text-gray-500 mt-1">
          Помогите модели стать точнее. Здесь показаны записи, в которых она наименее уверена.
          Ваши решения попадут в датасет для следующего fine-tune.
        </p>
        {stats && (
          <div className="mt-3 flex gap-4 text-sm">
            <span><strong>{stats.total}</strong> уже размечено</span>
          </div>
        )}
      </div>

      {loading && <div className="text-center text-gray-400 py-8">Загрузка…</div>}

      {!loading && queue.length === 0 && (
        <div className="bg-white rounded-xl shadow-sm p-8 text-center">
          <GovIcon name="check" className="mx-auto mb-2 h-7 w-7 text-green-600" />
          <p className="text-gray-500">Очередь пуста — все записи проверены</p>
        </div>
      )}

      {current && (
        <div className="bg-white rounded-xl shadow-sm overflow-hidden">
          <div className="px-5 py-3 border-b flex items-center justify-between bg-gray-50">
            <span className="text-xs text-gray-500">
              Запись {idx + 1} из {queue.length} · confidence {(current.confidence * 100).toFixed(1)}%
            </span>
            <span className="text-xs text-gray-500">#{current.appeal_id}</span>
          </div>

          <div className="px-5 py-4">
            <p className="text-xs text-gray-400 mb-1">{current.municipality} · группа из файла: {current.group_name || '—'}</p>
            <p className="text-sm text-gray-800 leading-relaxed mb-4">{current.incident_text}</p>

            <div className="text-xs text-gray-500 mb-3">
              Модель предсказала: <strong>{current.predicted_category || '—'}</strong> ·{' '}
              <strong>{current.predicted_severity ? formatSeverity(current.predicted_severity) : '—'}</strong>
            </div>

            <AnnotationForm
              categories={meta.categories}
              severities={meta.severities}
              initialCategory={current.predicted_category}
              initialSeverity={current.predicted_severity}
              onSubmit={submit}
              onSkip={() => setIdx(i => Math.min(i + 1, queue.length - 1))}
            />
          </div>
        </div>
      )}
    </div>
  )
}

function AnnotationForm({ categories, severities, initialCategory, initialSeverity, onSubmit, onSkip }) {
  const [category, setCategory] = useState(initialCategory || categories[0])
  const [severity, setSeverity] = useState(initialSeverity || 'MEDIUM')
  const [isProblem, setIsProblem] = useState(true)

  useEffect(() => {
    setCategory(initialCategory || categories[0])
    setSeverity(initialSeverity || 'MEDIUM')
    setIsProblem(true)
  }, [initialCategory, initialSeverity, categories])

  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs text-gray-500">Это проблема?</label>
        <div className="flex gap-2 mt-1">
          <button
            onClick={() => setIsProblem(true)}
            className={`px-3 py-1 text-xs rounded border ${isProblem ? 'bg-teal-600 text-white border-teal-600' : 'bg-white text-gray-700'}`}
          >
            Да
          </button>
          <button
            onClick={() => setIsProblem(false)}
            className={`px-3 py-1 text-xs rounded border ${!isProblem ? 'bg-gray-600 text-white border-gray-600' : 'bg-white text-gray-700'}`}
          >
            Нет (благодарность/инфо)
          </button>
        </div>
      </div>

      {isProblem && (
        <>
          <div>
            <label className="text-xs text-gray-500">Категория</label>
            <select
              value={category}
              onChange={e => setCategory(e.target.value)}
              className="block w-full text-sm border border-gray-200 rounded px-2 py-1.5 mt-1"
            >
              {categories.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-500">Тяжесть</label>
            <div className="flex gap-1 mt-1">
              {severities.map(s => (
                <button
                  key={s}
                  onClick={() => setSeverity(s)}
                  className={`flex-1 px-2 py-1 text-xs rounded border ${severity === s ? 'bg-teal-600 text-white border-teal-600' : 'bg-white text-gray-700'}`}
                >
                  {formatSeverity(s)}
                </button>
              ))}
            </div>
          </div>
        </>
      )}

      <div className="flex gap-2 pt-2 border-t">
        <button
          onClick={() => onSubmit({ category, severity, is_problem: isProblem })}
          className="flex flex-1 items-center justify-center gap-2 px-4 py-2 bg-[#0d7377] text-white rounded-lg text-sm font-medium hover:bg-[#0a5c5f]"
        >
          <GovIcon name="save" className="h-4 w-4" />
          Сохранить
        </button>
        <button
          onClick={onSkip}
          className="px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm text-gray-600 hover:bg-gray-50"
        >
          Пропустить
        </button>
      </div>
      <p className="pt-3 text-xs leading-relaxed text-gray-500">
        Остались вопросы？ Cвяжитесь с нами！<br />
        Tехническая поддержка techhelp@superbober.ru
      </p>
    </div>
  )
}
