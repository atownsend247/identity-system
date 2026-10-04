import { type FormEvent, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { api } from './api'
import { errorMessage, useAdminList } from './useAdminList'
import type { AdminOidcClient, IssuedOidcSecret, OidcScope } from './types'

const ALL_SCOPES: OidcScope[] = ['openid', 'email', 'profile']

interface ClientForm {
  client_id: string
  redirect_uris: string
  allowed_scopes: OidcScope[]
}

const EMPTY_FORM: ClientForm = { client_id: '', redirect_uris: '', allowed_scopes: ['openid'] }

// One redirect URI per line - the textarea is the only place these are typed,
// so parse it here rather than making every caller split it.
function parseRedirectUris(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

function AdminOidcClients() {
  const { items: clients, status, refresh } = useAdminList<AdminOidcClient>(
    '/api/admin/oidc-clients',
  )
  const [editing, setEditing] = useState<AdminOidcClient | null>(null)
  const [form, setForm] = useState<ClientForm>(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)
  // The plaintext secret is shown here once and then it's gone for good - the
  // server only keeps its hash.
  const [issued, setIssued] = useState<IssuedOidcSecret | null>(null)

  if (status === 'loading') return <p>Loading...</p>
  if (status === 'unauthenticated') {
    return <Navigate to="/login?rd=/admin/oidc-clients" replace />
  }
  if (status === 'forbidden') {
    return <p className="error">Forbidden - your account isn't on the admin allowlist.</p>
  }
  if (status === 'error') return <p className="error">Something went wrong.</p>

  const resetForm = () => {
    setEditing(null)
    setForm(EMPTY_FORM)
    setFormError(null)
  }

  const startEdit = (client: AdminOidcClient) => {
    setEditing(client)
    setForm({
      client_id: client.client_id,
      redirect_uris: client.redirect_uris.join('\n'),
      allowed_scopes: client.allowed_scopes,
    })
    setFormError(null)
    setIssued(null)
  }

  const toggleScope = (scope: OidcScope) => {
    const next = form.allowed_scopes.includes(scope)
      ? form.allowed_scopes.filter((s) => s !== scope)
      : [...form.allowed_scopes, scope]
    setForm({ ...form, allowed_scopes: next })
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setFormError(null)
    const body = {
      redirect_uris: parseRedirectUris(form.redirect_uris),
      allowed_scopes: form.allowed_scopes,
    }
    try {
      if (editing) {
        await api.put(`/api/admin/oidc-clients/${editing.client_id}`, body)
        resetForm()
      } else {
        const created = await api.post<AdminOidcClient & { client_secret: string }>(
          '/api/admin/oidc-clients',
          { client_id: form.client_id, ...body },
        )
        setIssued({ client_id: created.client_id, client_secret: created.client_secret })
        resetForm()
      }
      await refresh()
    } catch (err) {
      setFormError(errorMessage(err))
    }
  }

  const handleRotate = async (client: AdminOidcClient) => {
    if (
      !window.confirm(
        `Rotate the secret for "${client.client_id}"? The old one stops working immediately.`,
      )
    ) {
      return
    }
    setFormError(null)
    try {
      const rotated = await api.post<IssuedOidcSecret>(
        `/api/admin/oidc-clients/${client.client_id}/rotate-secret`,
      )
      setIssued(rotated)
    } catch (err) {
      setFormError(errorMessage(err))
    }
  }

  const handleDelete = async (client: AdminOidcClient) => {
    if (!window.confirm(`Delete the OIDC client "${client.client_id}"?`)) return
    setFormError(null)
    try {
      await api.delete(`/api/admin/oidc-clients/${client.client_id}`)
      if (editing?.client_id === client.client_id) resetForm()
      await refresh()
    } catch (err) {
      setFormError(errorMessage(err))
    }
  }

  return (
    <div>
      <h1>OIDC clients</h1>
      <p>
        Relying parties allowed to sign users in through this service's OpenID
        Connect endpoints. Every registered client is auto-approved - there's no
        consent screen.
      </p>

      {issued && (
        <div className="secret-banner" role="status">
          <p>
            <strong>Secret for {issued.client_id}:</strong>{' '}
            <code>{issued.client_secret}</code>
          </p>
          <p>Copy it now. It won't be shown again - only a hash is kept.</p>
          <button type="button" className="btn" onClick={() => setIssued(null)}>
            Dismiss
          </button>
        </div>
      )}

      {clients.length === 0 ? (
        <p>No OIDC clients registered yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Client ID</th>
              <th>Redirect URIs</th>
              <th>Scopes</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {clients.map((client) => (
              <tr key={client.client_id}>
                <td>
                  <code>{client.client_id}</code>
                </td>
                <td>
                  {client.redirect_uris.map((uri) => (
                    <div key={uri}>{uri}</div>
                  ))}
                </td>
                <td>{client.allowed_scopes.join(' ')}</td>
                <td>
                  <button type="button" className="btn" onClick={() => startEdit(client)}>
                    Edit
                  </button>{' '}
                  <button type="button" className="btn" onClick={() => handleRotate(client)}>
                    Rotate secret
                  </button>{' '}
                  <button type="button" className="btn" onClick={() => handleDelete(client)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>{editing ? `Edit ${editing.client_id}` : 'Register a client'}</h2>
      {formError && <p className="error">{formError}</p>}
      <form onSubmit={handleSubmit}>
        <label htmlFor="client-id">Client ID</label>
        <input
          id="client-id"
          type="text"
          value={form.client_id}
          onChange={(e) => setForm({ ...form, client_id: e.target.value })}
          disabled={editing !== null}
          required
        />
        <label htmlFor="redirect-uris">Redirect URIs (one per line)</label>
        <textarea
          id="redirect-uris"
          rows={3}
          value={form.redirect_uris}
          onChange={(e) => setForm({ ...form, redirect_uris: e.target.value })}
          required
        />
        <fieldset>
          <legend>Allowed scopes</legend>
          {ALL_SCOPES.map((scope) => (
            <label key={scope} className="checkbox-label">
              <input
                type="checkbox"
                checked={form.allowed_scopes.includes(scope)}
                onChange={() => toggleScope(scope)}
              />{' '}
              {scope}
            </label>
          ))}
        </fieldset>
        <p>
          <button type="submit" className="btn" disabled={form.allowed_scopes.length === 0}>
            {editing ? 'Save changes' : 'Register client'}
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

export default AdminOidcClients
