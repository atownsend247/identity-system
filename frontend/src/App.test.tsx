import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

function mockFetch(loggedIn: boolean, isAdmin = false) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string | URL) => {
      const path = url.toString()
      if (path.includes('/me')) {
        return {
          ok: loggedIn,
          status: loggedIn ? 200 : 401,
          json: async () =>
            loggedIn
              ? {
                  id: '1',
                  email: 'test@example.com',
                  name: 'Test',
                  totp_enabled: false,
                  is_admin: isAdmin,
                }
              : { detail: 'not authenticated' },
        } as Response
      }
      if (path.includes('/api/apps')) {
        return { ok: true, status: 200, json: async () => [] } as Response
      }
      return { ok: true, status: 200, json: async () => [] } as Response
    }),
  )
}

describe('App', () => {
  beforeEach(() => {
    // BrowserRouter shares real browser history across tests in this file.
    window.history.pushState({}, '', '/')
  })

  it('renders the Apps page by default whether or not the visitor is signed in', async () => {
    mockFetch(false)
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Apps' })).toBeInTheDocument()
  })

  it('shows a "Sign in" link when logged out', async () => {
    mockFetch(false)
    render(<App />)
    await waitFor(() => expect(screen.getByRole('link', { name: 'Sign in' })).toBeInTheDocument())
  })

  it('shows the user name and a logout control when logged in', async () => {
    mockFetch(true)
    render(<App />)
    expect(await screen.findByText('Test')).toBeInTheDocument()
    expect(screen.getByText('Log out')).toBeInTheDocument()
  })

  it('hides the admin links from a signed-in non-admin', async () => {
    mockFetch(true, false)
    render(<App />)
    await screen.findByText('Test')
    expect(screen.queryByRole('link', { name: 'Apps directory' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'OIDC clients' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument()
  })

  it('shows the admin links to an allowlisted admin', async () => {
    mockFetch(true, true)
    render(<App />)
    expect(await screen.findByRole('link', { name: 'Apps directory' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'OIDC clients' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Users' })).toBeInTheDocument()
  })
})
