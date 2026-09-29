import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Account from './Account'
import type { User } from './types'

function renderAccount(user: User | null, onUserUpdated = vi.fn()) {
  return {
    onUserUpdated,
    ...render(
      <MemoryRouter initialEntries={['/account']}>
        <Routes>
          <Route path="/account" element={<Account user={user} onUserUpdated={onUserUpdated} />} />
          <Route path="/login" element={<p>Login page</p>} />
        </Routes>
      </MemoryRouter>,
    ),
  }
}

const demoUser: User = { id: '1', email: 'a@b.com', name: 'A B', totp_enabled: true }

describe('Account', () => {
  it('redirects to /login?rd=/account when signed out', () => {
    renderAccount(null)
    expect(screen.getByText('Login page')).toBeInTheDocument()
  })

  it('shows the signed-in user\'s current name, email and 2FA status in the form fields', () => {
    renderAccount(demoUser)

    expect(screen.getByLabelText('Name')).toHaveValue('A B')
    expect(screen.getByLabelText('Email')).toHaveValue('a@b.com')
    expect(screen.getByText('Enabled')).toBeInTheDocument()
  })

  it('saves a new display name via PATCH /profile and refreshes the user', async () => {
    const fetchMock = vi.fn(async (url: string | URL, init?: RequestInit) => {
      const path = url.toString()
      if (path.includes('/profile')) {
        expect(init?.method).toBe('PATCH')
        expect(JSON.parse(init!.body as string)).toEqual({ name: 'New Name' })
        return { ok: true, status: 204, json: async () => undefined }
      }
      if (path.includes('/me')) {
        return { ok: true, status: 200, json: async () => ({ ...demoUser, name: 'New Name' }) }
      }
      throw new Error(`unexpected fetch: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const { onUserUpdated } = renderAccount(demoUser)

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'New Name' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[0])

    await waitFor(() =>
      expect(onUserUpdated).toHaveBeenCalledWith(expect.objectContaining({ name: 'New Name' })),
    )
  })

  it('requires the current password to save a new email', async () => {
    const fetchMock = vi.fn(async (url: string | URL, init?: RequestInit) => {
      const path = url.toString()
      if (path.includes('/profile')) {
        expect(JSON.parse(init!.body as string)).toEqual({
          email: 'new@b.com',
          current_password: 'secret123',
        })
        return { ok: true, status: 204, json: async () => undefined }
      }
      if (path.includes('/me')) {
        return { ok: true, status: 200, json: async () => ({ ...demoUser, email: 'new@b.com' }) }
      }
      throw new Error(`unexpected fetch: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const { onUserUpdated } = renderAccount(demoUser)

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'new@b.com' } })
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'secret123' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[1])

    await waitFor(() =>
      expect(onUserUpdated).toHaveBeenCalledWith(expect.objectContaining({ email: 'new@b.com' })),
    )
  })

  it('shows the server error when an email change is rejected', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 401, json: async () => ({ detail: 'incorrect password' }) })),
    )

    renderAccount(demoUser)

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'new@b.com' } })
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'wrong' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[1])

    expect(await screen.findByText('incorrect password')).toBeInTheDocument()
  })
})
