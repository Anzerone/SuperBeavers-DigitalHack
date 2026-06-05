export default function MetricCards({ stats }) {
  const cards = [
    {
      label: 'ОБЩАЯ ДЛИНА ДАТАСЕТА',
      value: stats.raw_records?.toLocaleString('ru') || '0',
      sub: 'строк в исходном файле',
      color: 'text-gray-800',
    },
    {
      label: 'ПРОБЛЕМНЫХ ОБРАЩЕНИЙ',
      value: stats.problem_count?.toLocaleString('ru') || '0',
      sub: `${stats.problem_percent}% помечены как проблема`,
      color: 'text-orange-600',
    },
    {
      label: 'КЛАСТЕРОВ',
      value: stats.cluster_count?.toLocaleString('ru') || '0',
      sub: 'по муниципалитетам',
      color: 'text-gray-800',
    },
  ]

  return (
    <div className="grid grid-cols-3 gap-3">
      {cards.map((c, i) => (
        <div key={i} className="bg-white rounded-xl p-4 shadow-sm">
          <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">{c.label}</p>
          <p className={`text-2xl font-bold ${c.color}`}>{c.value}</p>
          {c.sub && <p className="text-xs text-green-600 mt-1">{c.sub}</p>}
        </div>
      ))}
    </div>
  )
}
