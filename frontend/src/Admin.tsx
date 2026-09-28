import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { ApiError, api } from './api'
import type { AdminUser } from './types'

type Status = 'loading' | 'ok' | 'unauthenticated' | 'forbidden' | 'error'

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : 'never'
}

function Admin() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [status, setStatus] = useState<Status>('loading')

  useEffect(() => {
    api
      .get<AdminUser[]>('/api/admin/users')
      .then((data) => {
        setUsers(data)
        setStatus('ok')
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) setStatus('unauthenticated')
        else if (err instanceof ApiError && err.status === 403) setStatus('forbidden')
        else setStatus('error')
      })
  }, [])

  if (status === 'loading') return <p>Loading...</p>
  // Unlike GET /api/apps or GET /api/admin/users on the backend (which just
  // return a bare 401/403), a human browsing this page directly should be
  // sent to sign in - so the redirect-on-401 behavior lives here, not the
  // backend (see app.py's admin_users()).
  if (status === 'unauthenticated') return <Navigate to="/login?rd=/admin" replace />
  if (status === 'forbidden') {
    return <p className="error">Forbidden - your account isn't on the admin allowlist.</p>
  }
  if (status === 'error') return <p className="error">Something went wrong.</p>

  return (
    <div>
      <h1>Users</h1>
      <table>
        <thead>
          <tr>
            <th>Email</th>
            <th>Name</th>
            <th>Created</th>
            <th>Last login</th>
            <th>2FA</th>
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>{user.email}</td>
              <td>{user.name}</td>
              <td>{formatDate(user.created_at)}</td>
              <td>{formatDate(user.last_login_at)}</td>
              <td>{user.totp_enabled ? 'on' : 'off'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default Admin
