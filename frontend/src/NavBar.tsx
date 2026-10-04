import { NavLink } from 'react-router-dom'
import type { User } from './types'

interface Props {
  user: User | null
  onLogout: () => void
}

function NavBar({ user, onLogout }: Props) {
  return (
    <header className="app-header">
      <NavLink to="/">
        <strong>identity-system</strong>
      </NavLink>
      <nav>
        {user ? (
          <>
            {/* Admin pages are only linked for allowlisted accounts - the
                pages themselves are still gated server-side. */}
            {user.is_admin && (
              <>
                <NavLink to="/admin">Users</NavLink>{' '}
                <NavLink to="/admin/apps">Apps directory</NavLink>{' '}
                <NavLink to="/admin/oidc-clients">OIDC clients</NavLink>{' '}
              </>
            )}
            <NavLink to="/account">{user.name}</NavLink>{' '}
            <button type="button" className="btn" onClick={onLogout}>
              Log out
            </button>
          </>
        ) : (
          <NavLink to="/login">Sign in</NavLink>
        )}
      </nav>
    </header>
  )
}

export default NavBar
