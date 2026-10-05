import { type FormEvent, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { ApiError, api } from './api'
import type { User } from './types'

interface Props {
  user: User | null
  onUserUpdated: (user: User) => void
}

type Status = 'idle' | 'saving' | 'error'

function Account({ user, onUserUpdated }: Props) {
  // Hooks can't run conditionally, so these are declared before the
  // !user redirect below - `user` is only ever null for the one render
  // before that redirect takes effect.
  const [name, setName] = useState(user?.name ?? '')
  const [nameStatus, setNameStatus] = useState<Status>('idle')
  const [nameError, setNameError] = useState<string | null>(null)

  const [email, setEmail] = useState(user?.email ?? '')
  const [currentPassword, setCurrentPassword] = useState('')
  const [emailStatus, setEmailStatus] = useState<Status>('idle')
  const [emailError, setEmailError] = useState<string | null>(null)

  if (!user) return <Navigate to="/login?rd=/account" replace />

  // PATCH /profile has no response body worth trusting (see app.py's
  // update_profile) - re-fetch /me for the refreshed record, same as the
  // backend's own doc comment says a client should.
  const refreshUser = async () => onUserUpdated(await api.get<User>('/me'))

  const handleNameSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setNameStatus('saving')
    setNameError(null)
    try {
      await api.patch('/profile', { name })
      await refreshUser()
      setNameStatus('idle')
    } catch (err) {
      setNameError(err instanceof ApiError ? err.message : 'Something went wrong')
      setNameStatus('error')
    }
  }

  const handleEmailSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setEmailStatus('saving')
    setEmailError(null)
    try {
      await api.patch('/profile', { email, current_password: currentPassword })
      await refreshUser()
      setCurrentPassword('')
      setEmailStatus('idle')
    } catch (err) {
      setEmailError(err instanceof ApiError ? err.message : 'Something went wrong')
      setEmailStatus('error')
    }
  }

  return (
    <div>
      <h1>Account</h1>
      <dl>
        <dt>Two-factor authentication</dt>
        <dd>{user.totp_enabled ? 'Enabled' : 'Disabled'}</dd>
      </dl>

      <h2>Display name</h2>
      <form onSubmit={handleNameSubmit}>
        <label htmlFor="name">Name</label>
        <input id="name" value={name} onChange={(e) => setName(e.target.value)} required />
        <button type="submit" className="btn btn-primary" disabled={nameStatus === 'saving'}>
          Save
        </button>
        {nameError && <p className="error">{nameError}</p>}
      </form>

      <h2>Email address</h2>
      {/* sessionkit requires current_password to change email (unlike the
          name, which needs no reverification) - see CLAUDE.md's gotcha on
          set_email vs set_password. */}
      <form onSubmit={handleEmailSubmit}>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <label htmlFor="current-password">Current password</label>
        <input
          id="current-password"
          type="password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          required
        />
        <button type="submit" className="btn btn-primary" disabled={emailStatus === 'saving'}>
          Save
        </button>
        {emailError && <p className="error">{emailError}</p>}
      </form>

      <p>More account management (password, two-factor) is coming here later.</p>
    </div>
  )
}

export default Account
