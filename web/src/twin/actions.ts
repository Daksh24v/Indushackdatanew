// Imperative helpers shared by the HUD panels and in-scene popovers.
import { api } from '../lib/api'
import { useTwin } from '../lib/store'
import type { Building } from '../lib/types'
import { indexBuilding } from './geom'
import { useTwinUi } from './uiStore'

/** The default "you are here": a lobby/space next to a step-free entrance on the ground floor. */
export function defaultOrigin(b: Building): string | null {
  const idx = indexBuilding(b)
  const entrances = b.openings.filter((o) => o.space_ids.includes('outside'))
  const ranked = entrances
    .map((o) => {
      const sid = o.space_ids.find((s) => s !== 'outside')!
      const s = idx.spaces.get(sid)
      const f = s ? idx.floors.get(s.floor_id) : undefined
      let score = 0
      if (f && f.level === 0) score += 4
      if (o.step_free) score += 2
      if (s?.room_type === 'lobby' || s?.type === 'hall') score += 2
      if (s?.type === 'corridor') score += 1
      return { sid, score }
    })
    .filter((x) => idx.spaces.has(x.sid))
    .sort((a, b) => b.score - a.score)
  return ranked[0]?.sid ?? b.spaces[0]?.id ?? null
}

export function originOf(b: Building): string | null {
  return useTwinUi.getState().originId ?? defaultOrigin(b)
}

export async function navigate(
  siteId: string,
  to: string,
  opts: { from?: string; step_free?: boolean; min_width_m?: number } = {},
) {
  const b = useTwin.getState().building
  const from = opts.from || (b ? originOf(b) : null)
  if (!from) throw new Error('No start point')
  const body: { from: string; to: string; step_free?: boolean; min_width_m?: number } = { from, to }
  if (opts.step_free) body.step_free = true
  if (opts.min_width_m) body.min_width_m = opts.min_width_m
  const route = await api.route(siteId, body)
  useTwin.getState().setRoute(route)
  useTwin.getState().setFocusFloor(null)
  return route
}

export async function toggleClosed(siteId: string, id: string, closed: boolean) {
  const state = await api.setClosed(siteId, id, closed)
  useTwin.getState().setState(state)
  // Re-plan the visible route around the change.
  const route = useTwin.getState().route
  if (route?.reachable && route.space_path.length > 1) {
    try {
      const next = await api.route(siteId, {
        from: route.space_path[0], to: route.space_path[route.space_path.length - 1],
        step_free: route.step_free || undefined, min_width_m: route.min_width_m ?? undefined,
      })
      useTwin.getState().setRoute(next)
    } catch {
      /* keep the old route */
    }
  }
  return state
}
