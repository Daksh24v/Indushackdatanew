// Room scans (Gaussian splats via Spark) fitted into the plan, plus a wall-deviation heatmap.
import { Html } from '@react-three/drei'
import { useFrame, useThree } from '@react-three/fiber'
import { SparkRenderer, SplatMesh } from '@sparkjsdev/spark'
import { useEffect, useMemo, useState } from 'react'
import * as THREE from 'three'
import type { RoomScan, XY } from '../lib/types'
import { useTwin } from '../lib/store'
import { FloorAnchor } from './FloorAnchor'
import { indexBuilding } from './geom'
import { type TwinLayout, useLayout } from './layout'

export type Scan = Omit<RoomScan, 'deviations'> & {
  floor_id?: string
  deviations: { wall_index: number; offset_m: number; segment: [XY, XY]; direction?: string }[]
}

const DEMO_SCAN_URL = '/demo/room_scan.json'

/** GET /api/capture/rooms?site_id=, falling back to the bundled demo scan for its site. */
export function useRoomScans(siteId: string): Scan[] {
  const [scans, setScans] = useState<Scan[]>([])
  const captureEvents = useTwin((s) => s.events.filter((e) => e.type === 'capture').length)
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      let list: Scan[] = []
      try {
        const r = await fetch(`/api/capture/rooms?site_id=${encodeURIComponent(siteId)}`)
        if (r.ok) list = await r.json()
      } catch {
        /* server down */
      }
      if (!list.length) {
        try {
          const r = await fetch(DEMO_SCAN_URL)
          if (r.ok) {
            const demo = (await r.json()) as Scan
            if (demo.site_id === siteId) list = [demo]
          }
        } catch {
          /* no demo */
        }
      }
      if (!cancelled) setScans(list.filter((s) => s.splat_url))
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [siteId, captureEvents])
  return scans
}

export function RoomScans({ siteId }: { siteId: string }) {
  const scans = useRoomScans(siteId)
  const building = useTwin((s) => s.building)
  const layout = useLayout()
  if (!building || !scans.length) return null
  const idx = indexBuilding(building)
  // Newest scan per room only.
  const perRoom = new Map<string, Scan>()
  for (const s of scans) if (!perRoom.has(s.space_id)) perRoom.set(s.space_id, s)
  return (
    <>
      <SparkHost />
      {[...perRoom.values()].map((scan) => {
        const floorId = scan.floor_id ?? idx.spaces.get(scan.space_id)?.floor_id
        if (!floorId || !layout.byId.has(floorId)) return null
        return (
          <FloorAnchor key={scan.id} floorId={floorId}>
            <ScanView scan={scan} floorId={floorId} layout={layout} />
          </FloorAnchor>
        )
      })}
    </>
  )
}

/** One SparkRenderer per canvas, mounted only while scans exist. */
function SparkHost() {
  const gl = useThree((s) => s.gl)
  const scene = useThree((s) => s.scene)
  useEffect(() => {
    const spark = new SparkRenderer({ renderer: gl })
    scene.add(spark)
    return () => {
      scene.remove(spark)
      spark.dispose?.()
    }
  }, [gl, scene])
  return null
}

function ScanView({ scan, floorId, layout }: { scan: Scan; floorId: string; layout: TwinLayout }) {
  const elevation = layout.byId.get(floorId)!.floor.elevation_m
  const building = useTwin((s) => s.building)!
  const focus = useTwin((s) => s.focusFloorId)
  const splat = useMemo(() => {
    const mesh = new SplatMesh({ url: scan.splat_url })
    // transform: splat file → building frame (x, y, z-up, absolute metres). Then building → floor-local three.js.
    const toBuilding = new THREE.Matrix4().fromArray(scan.transform)
    const toLocal = new THREE.Matrix4().set(
      1, 0, 0, -layout.cx,
      0, 0, 1, -elevation,
      0, -1, 0, layout.cy,
      0, 0, 0, 1,
    )
    mesh.matrixAutoUpdate = false
    mesh.matrix.multiplyMatrices(toLocal, toBuilding)
    return mesh
  }, [scan.splat_url, scan.transform, layout, elevation])
  useEffect(() => () => splat.dispose(), [splat])

  const anchor = indexBuilding(building).anchors.get(scan.space_id)
  const [ax, az] = anchor ? layout.toXZ(anchor) : [0, 0]
  return (
    <group>
      <primitive object={splat} />
      {scan.deviations.map((d) => (
        <DeviationWall key={d.wall_index} seg={d.segment} offset={d.offset_m} direction={d.direction} layout={layout} showLabel={focus == null || focus === floorId} />
      ))}
      {(focus == null || focus === floorId) && (
        <Html position={[ax, 3.6, az]} center zIndexRange={[28, 0]} style={{ pointerEvents: 'none' }}>
          <div className="w-64 rounded-lg border border-violet-300/40 bg-slate-950/75 px-2.5 py-1.5 text-[11px] leading-snug text-violet-50 shadow-[0_0_24px_rgba(167,139,250,0.35)] backdrop-blur">
            <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wider text-violet-300">Room scan</div>
            {scan.summary}
          </div>
        </Html>
      )}
    </group>
  )
}

function deviationColor(off: number) {
  const a = Math.abs(off)
  return new THREE.Color(a < 0.1 ? '#34d399' : a < 0.25 ? '#fbbf24' : '#f43f5e')
}

function DeviationWall({ seg, offset, direction, layout, showLabel }: { seg: [XY, XY]; offset: number; direction?: string; layout: TwinLayout; showLabel: boolean }) {
  const [a, b] = seg
  const [ax, az] = layout.toXZ(a)
  const [bx, bz] = layout.toXZ(b)
  const len = Math.hypot(bx - ax, bz - az)
  const color = useMemo(() => deviationColor(offset), [offset])
  const mat = useMemo(
    () => new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.45, side: THREE.DoubleSide, toneMapped: false, depthWrite: false, blending: THREE.AdditiveBlending }),
    [color],
  )
  useEffect(() => () => mat.dispose(), [mat])
  const bad = Math.abs(offset) >= 0.25
  useFrame(({ clock }) => {
    const k = bad ? 0.5 + 0.5 * Math.sin(clock.elapsedTime * 4) : 0.6
    mat.color.copy(color).multiplyScalar(1.2 + 1.8 * k)
  })
  const angle = Math.atan2(-(bz - az), bx - ax)
  return (
    <group position={[(ax + bx) / 2, 0, (az + bz) / 2]} rotation-y={angle}>
      <mesh material={mat} position-y={1.4} renderOrder={12}>
        <planeGeometry args={[len, 2.8]} />
      </mesh>
      {showLabel && Math.abs(offset) >= 0.1 && (
        <Html position={[0, 3, 0]} center zIndexRange={[27, 0]} style={{ pointerEvents: 'none' }}>
          <div className="whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-semibold text-white" style={{ background: `#${color.getHexString()}cc` }}>
            {direction ? `${direction} ` : ''}
            {offset > 0 ? '+' : ''}
            {offset.toFixed(2)} m
          </div>
        </Html>
      )}
    </group>
  )
}
