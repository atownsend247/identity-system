import { useEffect, useState } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Account from './Account'
import Admin from './Admin'
import AdminApps from './AdminApps'
import AdminOidcClients from './AdminOidcClients'
import Apps from './Apps'
import Login from './Login'
import NavBar from './NavBar'
import { api } from './api'
import type { User } from './types'

function App() {
  const [user, setUser] = useState<User | null>(null)
  // Unlike a downstream app's own frontend (which requires auth for
  // everything and treats a 401 from GET /me as fatal), this app has to
  // render the Apps page whether or not the visitor is signed in - so a
  // 401 here is just "not logged in", not an error.
  const [checkedAuth, setCheckedAuth] = useState(false)

  useEffect(() => {
    api
      .get<User>('/me')
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setCheckedAuth(true))
  }, [])

  const handleLogout = () => {
    // A real top-level form submission, not fetch() - POST /logout
    // redirects to /login, which fetch() can't turn into a page navigation.
    const form = document.createElement('form')
    form.method = 'POST'
    form.action = '/logout'
    document.body.appendChild(form)
    form.submit()
  }

  if (!checkedAuth) return <p className="app-main">Loading...</p>

  return (
    <BrowserRouter>
      <NavBar user={user} onLogout={handleLogout} />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Apps />} />
          <Route path="/login" element={<Login />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="/admin/apps" element={<AdminApps />} />
          <Route path="/admin/oidc-clients" element={<AdminOidcClients />} />
          <Route path="/account" element={<Account user={user} onUserUpdated={setUser} />} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}

export default App
