import { Navigate } from 'react-router-dom'
import type { User } from './types'

interface Props {
  user: User | null
}

function Account({ user }: Props) {
  if (!user) return <Navigate to="/login?rd=/account" replace />

  return (
    <div>
      <h1>Account</h1>
      <dl>
        <dt>Name</dt>
        <dd>{user.name}</dd>
        <dt>Email</dt>
        <dd>{user.email}</dd>
        <dt>Two-factor authentication</dt>
        <dd>{user.totp_enabled ? 'Enabled' : 'Disabled'}</dd>
      </dl>
      {/* Read-only for now - editing (name/email/password/2FA), already
          possible via PATCH /profile etc., is a later pass. */}
      <p>More account management is coming here later.</p>
    </div>
  )
}

export default Account
