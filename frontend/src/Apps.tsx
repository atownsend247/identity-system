import { useEffect, useState } from 'react'
import { api } from './api'
import type { App as AppEntry } from './types'

function Apps() {
  const [apps, setApps] = useState<AppEntry[] | null>(null)

  useEffect(() => {
    api
      .get<AppEntry[]>('/api/apps')
      .then(setApps)
      .catch(() => setApps([]))
  }, [])

  return (
    <div>
      <h1>Apps</h1>
      {apps === null ? (
        <p>Loading...</p>
      ) : apps.length === 0 ? (
        <p>No apps configured yet.</p>
      ) : (
        <div className="apps-grid">
          {apps.map((app) => (
            // Plain <a>, not react-router's <Link> - these point at other
            // origins entirely. A logged-out click bounces through that
            // app's own forward-auth back to /login?rd=<that app> (see
            // README's "Wiring up a reverse proxy") - nothing special
            // needed here for either case.
            <a key={app.url} className="app-card" href={app.url}>
              <strong>{app.name}</strong>
              <p>{app.description}</p>
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

export default Apps
