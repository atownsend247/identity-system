import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Admin from './Admin'

function renderAdmin() {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <Routes>
        <Route path="/admin" element={<Admin />} />
        <Route path="/login" element={<p>Login page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Admin', () => {
  it('redirects to /login?rd=/admin when signed out', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 401, json: async () => ({}) })))

    renderAdmin()

    expect(await screen.findByText('Login page')).toBeInTheDocument()
  })

  it('shows a forbidden message for a signed-in non-admin', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 403, json: async () => ({ detail: 'forbidden' }) })),
    )

    renderAdmin()

    expect(await screen.findByText(/Forbidden/)).toBeInTheDocument()
  })

  it('renders the user table for an admin', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => [
          {
            id: '1',
            email: 'admin@example.com',
            name: 'Admin',
            created_at: '2026-01-01T00:00:00Z',
            last_login_at: '2026-02-01T00:00:00Z',
            totp_enabled: true,
          },
          {
            id: '2',
            email: 'nolog@example.com',
            name: 'Never Logged In',
            created_at: '2026-01-02T00:00:00Z',
            last_login_at: null,
            totp_enabled: false,
          },
        ],
      })),
    )

    renderAdmin()

    expect(await screen.findByText('admin@example.com')).toBeInTheDocument()
    expect(screen.getByText('nolog@example.com')).toBeInTheDocument()
    expect(screen.getByText('never')).toBeInTheDocument()
  })
})
