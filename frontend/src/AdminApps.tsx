import { type FormEvent, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { api } from './api'
import { errorMessage, useAdminList } from './useAdminList'
import type { AdminApp } from './types'

interface AppForm {
  name: string
  url: string
  description: string
}

const EMPTY_FORM: AppForm = { name: '', url: '', description: '' }

function AdminApps() {
  const { items: apps, status, refresh } = useAdminList<AdminApp>('/api/admin/apps')
  const [editing, setEditing] = useState<AdminApp | null>(null)
  const [form, setForm] = useState<AppForm>(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)

  if (status === 'loading') return <p>Loading...</p>
  if (status === 'unauthenticated') return <Navigate to="/login?rd=/admin/apps" replace />
  if (status === 'forbidden') {
    return <p className="error">Forbidden - your account isn't on the admin allowlist.</p>
  }
  if (status === 'error') return <p className="error">Something went wrong.</p>

  const resetForm = () => {
    setEditing(null)
    setForm(EMPTY_FORM)
    setFormError(null)
  }

  const startEdit = (app: AdminApp) => {
    setEditing(app)
    setForm({ name: app.name, url: app.url, description: app.description })
    setFormError(null)
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setFormError(null)
    try {
      if (editing) await api.put(`/api/admin/apps/${editing.id}`, form)
      else await api.post('/api/admin/apps', form)
      resetForm()
      await refresh()
    } catch (err) {
      setFormError(errorMessage(err))
    }
  }

  const handleDelete = async (app: AdminApp) => {
    if (!window.confirm(`Remove "${app.name}" from the apps directory?`)) return
    setFormError(null)
    try {
      await api.delete(`/api/admin/apps/${app.id}`)
      if (editing?.id === app.id) resetForm()
      await refresh()
    } catch (err) {
      setFormError(errorMessage(err))
    }
  }

  return (
    <div>
      <h1>Apps directory</h1>
      <p>
        The cards on the home page. Changes show up immediately - no restart or
        redeploy needed.
      </p>

      {apps.length === 0 ? (
        <p>No apps yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>URL</th>
              <th>Description</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {apps.map((app) => (
              <tr key={app.id}>
                <td>{app.name}</td>
                <td>
                  <a href={app.url}>{app.url}</a>
                </td>
                <td>{app.description}</td>
                <td>
                  <button type="button" className="btn" onClick={() => startEdit(app)}>
                    Edit
                  </button>{' '}
                  <button type="button" className="btn btn-danger" onClick={() => handleDelete(app)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>{editing ? `Edit ${editing.name}` : 'Add an app'}</h2>
      {formError && <p className="error">{formError}</p>}
      <form onSubmit={handleSubmit}>
        <label htmlFor="app-name">Name</label>
        <input
          id="app-name"
          type="text"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          required
        />
        <label htmlFor="app-url">URL</label>
        <input
          id="app-url"
          type="url"
          placeholder="https://"
          value={form.url}
          onChange={(e) => setForm({ ...form, url: e.target.value })}
          required
        />
        <label htmlFor="app-description">Description</label>
        <input
          id="app-description"
          type="text"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
        />
        <p>
          <button type="submit" className="btn btn-primary">
            {editing ? 'Save changes' : 'Add app'}
          </button>{' '}
          {editing && (
            <button type="button" className="btn" onClick={resetForm}>
              Cancel
            </button>
          )}
        </p>
      </form>
    </div>
  )
}

export default AdminApps
