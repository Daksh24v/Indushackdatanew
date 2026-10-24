// Owner: agent C. QR code that opens /capture?site=<id> on a phone. Rendered by SitePage (agent B).
export default function CaptureQR({ siteId }: { siteId: string }) {
  return <div>QR for {siteId} (agent C)</div>
}
