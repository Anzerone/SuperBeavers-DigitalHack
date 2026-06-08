import { useEffect, useMemo, useRef, useState } from 'react'
import Map from 'ol/Map.js'
import View from 'ol/View.js'
import Feature from 'ol/Feature.js'
import Overlay from 'ol/Overlay.js'
import Point from 'ol/geom/Point.js'
import TileLayer from 'ol/layer/Tile.js'
import VectorLayer from 'ol/layer/Vector.js'
import OSM from 'ol/source/OSM.js'
import VectorSource from 'ol/source/Vector.js'
import { Circle as CircleStyle, Fill, Stroke, Style } from 'ol/style.js'
import { boundingExtent } from 'ol/extent.js'
import { fromLonLat } from 'ol/proj.js'
import 'ol/ol.css'

// Координаты центров 34 муниципалитетов Омской области.
// Источник: открытые геоданные / OSM.
const MUNI_CENTROIDS = {
  'Омск г.о.': [54.9885, 73.3242],
  'Азовский немецкий национальный район': [54.7080, 73.0500],
  'Большереченский район': [56.0975, 74.6325],
  'Большеуковский район': [56.7800, 72.6000],
  'Горьковский район': [55.2842, 73.6342],
  'Знаменский район': [57.1500, 74.0500],
  'Исилькульский район': [54.9131, 71.2856],
  'Калачинский район': [55.0500, 74.5781],
  'Колосовский район': [56.4633, 73.5917],
  'Кормиловский район': [55.0500, 74.0789],
  'Крутинский район': [56.0094, 71.5170],
  'Любинский район': [55.1525, 72.6594],
  'Марьяновский район': [54.9389, 72.6692],
  'Москаленский район': [54.9817, 71.8989],
  'Муромцевский район': [56.3700, 75.2700],
  'Называевский район': [55.5750, 71.3550],
  'Нижнеомский район': [55.4267, 74.9892],
  'Нововаршавский район': [54.0708, 74.7233],
  'Одесский район': [54.2228, 72.9533],
  'Оконешниковский район': [54.8344, 75.1006],
  'Омский район': [54.9885, 73.3242],
  'Павлоградский район': [54.1981, 73.5611],
  'Полтавский район': [54.3733, 71.7639],
  'Русско-Полянский район': [53.7900, 73.9100],
  'Саргатский район': [55.6133, 73.4978],
  'Седельниковский район': [56.9528, 75.3119],
  'Таврический район': [54.5867, 73.6494],
  'Тарский район': [56.8989, 74.3697],
  'Тевризский район': [57.5000, 72.4000],
  'Тюкалинский район': [55.8689, 72.1925],
  'Усть-Ишимский район': [57.6892, 71.1639],
  'Черлакский район': [54.1500, 74.7833],
  'Шербакульский район': [54.3197, 72.3989],
  'Омская область, другое': [55.5, 73.5],
}

function color(intensity) {
  const r = Math.round(255 * Math.min(1, intensity * 1.8))
  const g = Math.round(255 * (1 - Math.max(0, (intensity - 0.5) * 2)))
  return `rgb(${r}, ${g}, 80)`
}

function alphaColor(rgbColor, alpha) {
  return rgbColor.replace('rgb', 'rgba').replace(')', `, ${alpha})`)
}

function markerStyle(point, activeMunicipality) {
  const active = Array.isArray(activeMunicipality)
    ? activeMunicipality.includes(point.municipality)
    : activeMunicipality === point.municipality
  return new Style({
    image: new CircleStyle({
      radius: point.radius,
      fill: new Fill({ color: alphaColor(point.color, active ? 0.9 : 0.55) }),
      stroke: new Stroke({ color: point.color, width: active ? 3 : 1.5 }),
    }),
  })
}

export default function OmskMap({ districts = [], onSelect, activeMunicipality }) {
  const containerRef = useRef(null)
  const tooltipRef = useRef(null)
  const mapRef = useRef(null)
  const sourceRef = useRef(new VectorSource())
  const overlayRef = useRef(null)
  const onSelectRef = useRef(onSelect)
  const [tooltipPoint, setTooltipPoint] = useState(null)

  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])

  const points = useMemo(() => {
    const maxCount = Math.max(1, ...districts.map(d => d.count))
    return districts
      .map(d => {
        const pos = MUNI_CENTROIDS[d.municipality]
        if (!pos) return null
        const norm = d.count / maxCount
        return {
          municipality: d.municipality,
          count: d.count,
          position: pos,
          radius: 6 + Math.sqrt(norm) * 22,
          color: color(norm),
        }
      })
      .filter(Boolean)
  }, [districts])

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    overlayRef.current = new Overlay({
      element: tooltipRef.current,
      offset: [0, -12],
      positioning: 'bottom-center',
    })

    const vectorLayer = new VectorLayer({
      source: sourceRef.current,
    })

    const map = new Map({
      target: containerRef.current,
      layers: [
        new TileLayer({
          source: new OSM({
            attributions: '© OpenStreetMap',
          }),
        }),
        vectorLayer,
      ],
      overlays: [overlayRef.current],
      view: new View({
        center: fromLonLat([73.5, 55.5]),
        zoom: 6,
      }),
    })

    map.on('pointermove', event => {
      const feature = map.forEachFeatureAtPixel(event.pixel, f => f)
      const point = feature?.get('point')
      map.getTargetElement().style.cursor = point ? 'pointer' : ''
      setTooltipPoint(point || null)
      if (point) overlayRef.current.setPosition(event.coordinate)
    })

    map.on('click', event => {
      const feature = map.forEachFeatureAtPixel(event.pixel, f => f)
      const point = feature?.get('point')
      if (point) onSelectRef.current?.(point.municipality)
    })

    mapRef.current = map

    return () => {
      map.setTarget(undefined)
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const source = sourceRef.current
    source.clear()
    const features = points.map(point => {
      const feature = new Feature({
        geometry: new Point(fromLonLat([point.position[1], point.position[0]])),
      })
      feature.set('point', point)
      feature.setStyle(markerStyle(point, activeMunicipality))
      return feature
    })
    source.addFeatures(features)

    if (mapRef.current && features.length) {
      const selected = Array.isArray(activeMunicipality)
        ? activeMunicipality
        : (activeMunicipality ? [activeMunicipality] : [])

      // При выборе района приближаемся к выбранным точкам, иначе показываем все.
      const focusFeatures = selected.length
        ? features.filter(f => selected.includes(f.get('point').municipality))
        : features
      const target = focusFeatures.length ? focusFeatures : features

      const coordinates = target.map(feature => feature.getGeometry().getCoordinates())
      const extent = boundingExtent(coordinates)
      mapRef.current.getView().fit(extent, {
        padding: [60, 60, 60, 60],
        maxZoom: selected.length ? 10 : 8,
        duration: 350,
      })
    }
  }, [points, activeMunicipality])

  return (
    <div className="bg-white rounded-xl shadow-sm overflow-hidden">
      <div className="px-4 py-3 border-b flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-700">Карта обращений по Омской области</h3>
          <p className="text-[11px] text-gray-400">Радиус и цвет круга — количество проблемных обращений</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span className="w-2.5 h-2.5 rounded-full bg-green-400" />низко
          <span className="w-2.5 h-2.5 rounded-full bg-yellow-400" />средне
          <span className="w-2.5 h-2.5 rounded-full bg-red-500" />высоко
        </div>
      </div>
      <div ref={containerRef} className="relative h-[380px] w-full" />
      <div
        ref={tooltipRef}
        className={`pointer-events-none rounded-md bg-white px-2 py-1 text-xs shadow-lg ring-1 ring-gray-200 ${tooltipPoint ? '' : 'hidden'}`}
      >
        {tooltipPoint && (
          <>
            <div className="font-semibold text-gray-800">{tooltipPoint.municipality}</div>
            <div className="text-gray-600">{tooltipPoint.count.toLocaleString('ru')} обращений</div>
          </>
        )}
      </div>
    </div>
  )
}
