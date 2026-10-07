import { type FormEvent, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { api } from './api'
import { errorMessage, useAdminList, type AdminStatus } from './useAdminList'
import type { AdminUser } from './types'

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : 'never'
}

function Admin() {
  // Unlike GET /api/apps or GET /api/admin/users on the backend (which just
  // return a bare 401/403), a human browsing this page directly should be
  // sent to sign in - so the redirect-on-401 behavior lives here, not the
  // backend (see app.py's require_admin).
  const { items: users, status, refresh } = useAdminList<AdminUser>('/api/admin/users')

  if (status === 'loading') return <p>Loading...</p>
  return <AdminView status={status} users={users} onUserCreated={refresh} />
}

interface NewUserForm {
  email: string
  name: string
  password: string
  confirmPassword: string
}

const EMPTY_FORM: NewUserForm = { email: '', name: '', password: '', confirmPassword: '' }

function AdminView({
  status,
  users,
  onUserCreated,
}: {
  status: AdminStatus
  users: AdminUser[]
  onUserCreated: () => Promise<void>
}) {
  const [form, setForm] = useState<NewUserForm>(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (status === 'unauthenticated') return <Navigate to="/login?rd=/admin" replace />
  if (status === 'forbidden') {
    return <p className="error">Forbidden - your account isn't on the admin allowlist.</p>
  }
  if (status === 'error') return <p className="error">Something went wrong.</p>

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setFormError(null)
    if (form.password !== form.confirmPassword) {
      setFormError('Passwords do not match')
      return
    }
    setSubmitting(true)
    try {
      // There's no self-registration endpoint, deliberately (see CLAUDE.md) -
      // this is the admin-only equivalent of `sessionkit add`, not a signup
      // form. Validation (email format, password strength) lives in
      // AuthService.create_user, same as everywhere else.
      await api.post('/api/admin/users', {
        email: form.email,
        password: form.password,
        name: form.name || undefined,
      })
      setForm(EMPTY_FORM)
      await onUserCreated()
    } catch (err) {
      setFormError(errorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div>
      <h1>Users</h1>
      {users.length === 0 ? (
        <p>No users yet.</p>
      ) : (
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
      )}

      <h2>Add a user</h2>
      {formError && <p className="error">{formError}</p>}
      <form onSubmit={handleSubmit}>
        <label htmlFor="new-user-email">Email</label>
        <input
          id="new-user-email"
          type="email"
          value={form.email}
          onChange={(e) => setForm({ ...form, email: e.target.value })}
          required
        />
        <label htmlFor="new-user-name">Name (optional)</label>
        <input
          id="new-user-name"
          type="text"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder="defaults to the email's local part"
        />
        <label htmlFor="new-user-password">Password</label>
        <input
          id="new-user-password"
          type="password"
          value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })}
          required
        />
        <label htmlFor="new-user-confirm-password">Confirm password</label>
        <input
          id="new-user-confirm-password"
          type="password"
          value={form.confirmPassword}
          onChange={(e) => setForm({ ...form, confirmPassword: e.target.value })}
          required
        />
        <p>
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            Add user
          </button>
        </p>
      </form>
    </div>
  )
}

export default Admin
