import type { Delivery } from '../lib/types'

// Owner: agent C. Result card for one delivery (dimensions + cross-check). Rendered by SitePage (agent B).
export default function DeliveryCard({ delivery }: { delivery: Delivery }) {
  return <div>{delivery.label} (agent C)</div>
}
