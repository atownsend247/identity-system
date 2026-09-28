import { type FormEvent, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ApiError, api } from './api'
import type { User } from './types'

interface LoginResponse {
  user: User
  redirect_to: string
}

function Login() {
  const [searchParams] = useSearchParams()
  const rd = searchParams.get('rd') ?? ''
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [otp, setOtp] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const result = await api.post<LoginResponse>('/api/login', { email, password, otp, rd })
      // redirect_to is computed server-side (Settings.is_trusted_redirect) -
      // navigate to that, never to the raw ?rd= itself, or the
      // open-redirect guard is for nothing (see CLAUDE.md).
      window.location.assign(result.redirect_to)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong')
      setSubmitting(false)
    }
  }

  return (
    <div>
      <h1>Sign in</h1>
      {error && <p className="error">{error}</p>}
      <form onSubmit={handleSubmit}>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoFocus
        />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <label htmlFor="otp">2FA code (only if you have it enabled)</label>
        <input
          id="otp"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          value={otp}
          onChange={(e) => setOtp(e.target.value)}
        />
        <button type="submit" className="btn" disabled={submitting}>
          Sign in
        </button>
      </form>
    </div>
  )
}

export default Login
