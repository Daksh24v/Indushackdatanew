import type { Site } from '../lib/types'

// Owner: agent D. Create a Standort at a clicked map position from a building.json or floor-plan image.
export default function AddSiteDialog(props: { lat: number; lon: number; onClose: () => void; onCreated: (site: Site) => void }) {
  return <div onClick={props.onClose}>Add site at {props.lat}, {props.lon} (agent D)</div>
}
