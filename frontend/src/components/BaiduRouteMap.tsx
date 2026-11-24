import { EnvironmentOutlined, LoadingOutlined } from '@ant-design/icons'
import { useEffect, useMemo, useRef, useState } from 'react'

import { agentApiUrl } from '../services/agentApi'

type Point = [longitude: number, latitude: number]

export interface RouteGeometry {
  origin: Point
  destination: Point
  path: Point[]
}

interface BaiduPoint {
  lng: number
  lat: number
}

interface BaiduMap {
  centerAndZoom(point: BaiduPoint, zoom: number): void
  addOverlay(overlay: object): void
  addControl(control: object): void
  setViewport(points: BaiduPoint[], options: { margins: number[]; enableAnimation: boolean }): void
  destroy(): void
}

interface BaiduSdk {
  Map: new (container: HTMLElement) => BaiduMap
  Point: new (...point: Point) => BaiduPoint
  Marker: new (point: BaiduPoint, options: { title: string }) => object
  Polyline: new (points: BaiduPoint[], options: { strokeColor: string; strokeWeight: number; strokeOpacity: number }) => object
  ZoomControl: new () => object
}

const mapWindow = window as Window & { BMap?: BaiduSdk; workbenchBaiduMapReady?: () => void }
const browserAk = import.meta.env.VITE_BAIDU_MAPS_BROWSER_AK?.trim()
let sdkPromise: Promise<BaiduSdk> | undefined

function loadMapSdk() {
  if (mapWindow.BMap?.Map) return Promise.resolve(mapWindow.BMap)
  if (sdkPromise) return sdkPromise
  sdkPromise = new Promise<BaiduSdk>((resolve, reject) => {
    const script = document.createElement('script')
    const finish = (api?: BaiduSdk) => {
      window.clearTimeout(timeout)
      if (api?.Map) {
        delete mapWindow.workbenchBaiduMapReady
        resolve(api)
      } else {
        mapWindow.workbenchBaiduMapReady = () => {}
        script.remove()
        sdkPromise = undefined
        reject(new Error('百度地图组件未能加载'))
      }
    }
    const timeout = window.setTimeout(() => finish(), 7000)
    mapWindow.workbenchBaiduMapReady = () => finish(mapWindow.BMap)
    script.onerror = () => finish()
    script.async = true
    script.src = `https://api.map.baidu.com/api?${new URLSearchParams({
      v: '4.0', ak: browserAk ?? '', callback: 'workbenchBaiduMapReady',
    })}`
    document.head.append(script)
  })
  return sdkPromise
}

function linkPoint(value: string | null): Point | undefined {
  if (!value?.startsWith('latlng:')) return
  const [coordinates] = value.slice(7).split('|')
  const parts = coordinates.split(',')
  if (parts.length !== 2 || parts.some(part => !part.trim())) return
  const [lat, lng] = parts.map(Number)
  if (Math.abs(lat) <= 90 && Math.abs(lng) <= 180) return [lng, lat]
}

function geometryFromBaiduLink(href?: string): RouteGeometry | undefined {
  if (!href) return
  const url = new URL(href)
  if (url.origin !== 'https://api.map.baidu.com' || url.pathname !== '/direction'
    || url.searchParams.get('coord_type') !== 'bd09ll') return
  const origin = linkPoint(url.searchParams.get('origin'))
  const destination = linkPoint(url.searchParams.get('destination'))
  if (origin && destination) return { origin, destination, path: [] }
}

type MapStatus = { type: 'loading' | 'interactive' | 'unavailable' } | { type: 'image'; src: string }

export function BaiduRouteMap({ geometry: receipt, sourceUrl, label }: {
  geometry?: RouteGeometry
  sourceUrl?: string
  label: string
}) {
  const host = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState<MapStatus>({ type: 'loading' })
  const geometry = useMemo(() => receipt ?? geometryFromBaiduLink(sourceUrl), [receipt, sourceUrl])
  useEffect(() => {
    if (!geometry) return
    const route = geometry
    const controller = new AbortController()
    let map: BaiduMap | undefined
    let preview: string | undefined
    let active = true

    async function renderMap() {
      if (!active) return
      const container = host.current
      if (container && browserAk && route.path.length >= 2) {
        try {
          const sdk = await loadMapSdk()
          if (!active) return
          const start = new sdk.Point(...route.origin)
          const end = new sdk.Point(...route.destination)
          const path = route.path.map(point => new sdk.Point(...point))
          map = new sdk.Map(container)
          map.centerAndZoom(start, 13)
          map.addOverlay(new sdk.Polyline(path, { strokeColor: '#6750db', strokeWeight: 5, strokeOpacity: 0.95 }))
          map.addOverlay(new sdk.Marker(start, { title: '起点' }))
          map.addOverlay(new sdk.Marker(end, { title: '终点' }))
          map.addControl(new sdk.ZoomControl())
          map.setViewport([start, ...path, end], { margins: [32, 32, 36, 32], enableAnimation: false })
          setStatus({ type: 'interactive' })
          return
        } catch {
          map?.destroy()
          map = undefined
        }
      }
      if (!active) return
      const response = await fetch(agentApiUrl('/api/v1/workspace/travel/map-preview'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(route),
        signal: controller.signal,
      })
      if (!response.ok) throw new Error('地图加载失败')
      const png = await response.blob()
      if (!active) return
      preview = URL.createObjectURL(png)
      setStatus({ type: 'image', src: preview })
    }

    // Let an immediate cleanup cancel the request before it reaches the provider.
    void Promise.resolve().then(renderMap).catch(() => {
      if (active) setStatus({ type: 'unavailable' })
    })
    return () => {
      active = false
      controller.abort()
      map?.destroy()
      if (preview) URL.revokeObjectURL(preview)
    }
  }, [geometry])

  if (!geometry) return null
  return (
    <div className="travel-map-canvas">
      <div ref={host} className="travel-map-host" role="region" aria-label={label}
        aria-hidden={status.type !== 'interactive'} />
      {status.type === 'image' && <img src={status.src} alt={label} width={640} height={340} />}
      {status.type === 'loading' && <div className="travel-map-state" role="status">
        <LoadingOutlined aria-hidden="true" /> 正在加载百度地图…
      </div>}
      {status.type === 'unavailable' && <div className="travel-map-state" role="status">
        <EnvironmentOutlined aria-hidden="true" /> 地图暂时无法加载，可打开百度地图查看。
      </div>}
    </div>
  )
}
