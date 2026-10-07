import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Admin from './Admin'

afterEach(() => {
  vi.unstubAllGlobals()
})

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

  it('creates a user with a POST and reloads the list', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        return {
          ok: true,
          status: 201,
          json: async () => ({
            id: '3',
            email: 'new@example.com',
            name: 'New Person',
            created_at: '2026-01-03T00:00:00Z',
            last_login_at: null,
            totp_enabled: false,
          }),
        }
      }
      return { ok: true, status: 200, json: async () => [] }
    })
    vi.stubGlobal('fetch', fetchMock)

    renderAdmin()
    await screen.findByText('No users yet.')

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'new@example.com' } })
    fireEvent.change(screen.getByLabelText('Name (optional)'), {
      target: { value: 'New Person' },
    })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password123' } })
    fireEvent.change(screen.getByLabelText('Confirm password'), {
      target: { value: 'password123' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add user' }))

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
      expect(post).toBeDefined()
      expect(JSON.parse(post![1]!.body as string)).toEqual({
        email: 'new@example.com',
        password: 'password123',
        name: 'New Person',
      })
    })
  })

  it('refuses to submit when the passwords do not match, without calling the API', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
      ok: true,
      status: 200,
      json: async () => [],
    }))
    vi.stubGlobal('fetch', fetchMock)

    renderAdmin()
    await screen.findByText('No users yet.')

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'new@example.com' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password123' } })
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'nope' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add user' }))

    expect(await screen.findByText('Passwords do not match')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('shows the server error when a duplicate email is rejected', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method === 'POST') {
          return {
            ok: false,
            status: 409,
            statusText: 'Conflict',
            json: async () => ({ detail: 'an account with that email already exists' }),
          }
        }
        return { ok: true, status: 200, json: async () => [] }
      }),
    )

    renderAdmin()
    await screen.findByText('No users yet.')

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'dup@example.com' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password123' } })
    fireEvent.change(screen.getByLabelText('Confirm password'), {
      target: { value: 'password123' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add user' }))

    expect(
      await screen.findByText('an account with that email already exists'),
    ).toBeInTheDocument()
  })
})
