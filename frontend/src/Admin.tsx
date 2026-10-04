import { Navigate } from 'react-router-dom'
import { useAdminList, type AdminStatus } from './useAdminList'
import type { AdminUser } from './types'

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : 'never'
}

function Admin() {
  // Unlike GET /api/apps or GET /api/admin/users on the backend (which just
  // return a bare 401/403), a human browsing this page directly should be
  // sent to sign in - so the redirect-on-401 behavior lives here, not the
  // backend (see app.py's require_admin).
  const { items: users, status } = useAdminList<AdminUser>('/api/admin/users')

  if (status === 'loading') return <p>Loading...</p>
  return <AdminView status={status} users={users} />
}

function AdminView({ status, users }: { status: AdminStatus; users: AdminUser[] }) {
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
