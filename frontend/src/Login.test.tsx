import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Login from './Login'

const realLocation = window.location

afterEach(() => {
  Object.defineProperty(window, 'location', { value: realLocation, writable: true })
})

function renderLogin(rd = '') {
  return render(
    <MemoryRouter initialEntries={[`/login${rd ? `?rd=${encodeURIComponent(rd)}` : ''}`]}>
      <Login />
    </MemoryRouter>,
  )
}

describe('Login', () => {
  it('submits credentials as JSON and navigates to the server-given redirect_to', async () => {
    const assign = vi.fn()
    Object.defineProperty(window, 'location', { value: { ...realLocation, assign }, writable: true })

    const fetchMock = vi.fn(async (_url: string | URL, _init?: RequestInit) => ({
      ok: true,
      status: 200,
      json: async () => ({
        user: { id: '1', email: 'a@b.com', name: 'A', totp_enabled: false },
        redirect_to: '/account',
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    renderLogin('https://evil.example.net/phish')

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.com' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(assign).toHaveBeenCalledWith('/account'))

    const [, init] = fetchMock.mock.calls[0]
    const body = JSON.parse(init!.body as string)
    expect(body).toMatchObject({ email: 'a@b.com', password: 'password123' })
  })

  it('shows the server error message on a failed login', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'incorrect email or password' }),
      })),
    )

    renderLogin()

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.com' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('incorrect email or password')).toBeInTheDocument()
  })
})
