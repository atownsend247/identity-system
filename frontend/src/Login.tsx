import { type FormEvent, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ApiError, api } from './api'
import type { User } from './types'

interface LoginResponse {
  user: User
  redirect_to: string
}

// Step 1 is email + password. The server answers 401 { otp_required: true }
// (and creates no session) when the password is right but the account has 2FA
// on - only then do we show step 2. sessionkit has no "password verified,
// code pending" state, so the password is kept here and re-sent with the code.
type Step = 'credentials' | 'otp'

function Login() {
  const [searchParams] = useSearchParams()
  const rd = searchParams.get('rd') ?? ''
  const [step, setStep] = useState<Step>('credentials')
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
      const body = step === 'otp' ? { email, password, otp, rd } : { email, password, rd }
      const result = await api.post<LoginResponse>('/api/login', body)
      // redirect_to is computed server-side (Settings.is_trusted_redirect) -
      // navigate to that, never to the raw ?rd= itself, or the
      // open-redirect guard is for nothing (see CLAUDE.md).
      window.location.assign(result.redirect_to)
    } catch (err) {
      if (step === 'credentials' && err instanceof ApiError && err.body.otp_required === true) {
        setStep('otp')
        setSubmitting(false)
        return
      }
      setError(err instanceof ApiError ? err.message : 'Something went wrong')
      setSubmitting(false)
    }
  }

  const handleBack = () => {
    setStep('credentials')
    setOtp('')
    setError(null)
  }

  return (
    <div>
      <h1>Sign in</h1>
      {error && <p className="error">{error}</p>}
      {step === 'credentials' ? (
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
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            Sign in
          </button>
        </form>
      ) : (
        <form onSubmit={handleSubmit}>
          <p>Enter the 6-digit code from your authenticator app, or a recovery code.</p>
          <label htmlFor="otp">2FA code</label>
          <input
            id="otp"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={otp}
            onChange={(e) => setOtp(e.target.value)}
            required
            autoFocus
          />
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            Verify
          </button>
          <button type="button" className="btn" onClick={handleBack} disabled={submitting}>
            Back
          </button>
        </form>
      )}
    </div>
  )
}

export default Login
