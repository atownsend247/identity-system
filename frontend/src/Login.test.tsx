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
    expect(screen.queryByLabelText('2FA code')).not.toBeInTheDocument()
  })

  it('never shows the 2FA step for a user without 2FA', async () => {
    const assign = vi.fn()
    Object.defineProperty(window, 'location', { value: { ...realLocation, assign }, writable: true })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ user: { id: '1' }, redirect_to: '/account' }),
      })),
    )

    renderLogin()
    expect(screen.queryByLabelText('2FA code')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.com' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(assign).toHaveBeenCalledWith('/account'))
    expect(screen.queryByLabelText('2FA code')).not.toBeInTheDocument()
  })

  it('moves to the 2FA step when required, then re-sends the password with the code', async () => {
    const assign = vi.fn()
    Object.defineProperty(window, 'location', { value: { ...realLocation, assign }, writable: true })
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'enter the 6-digit code', otp_required: true }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({ detail: 'incorrect authentication code' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ user: { id: '1' }, redirect_to: '/account' }),
      })
    vi.stubGlobal('fetch', fetchMock)

    renderLogin()
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.com' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    // step 2 appears with no error banner
    const otpInput = await screen.findByLabelText('2FA code')
    expect(screen.queryByText('enter the 6-digit code')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()

    // wrong code: error shown, still on step 2
    fireEvent.change(otpInput, { target: { value: '000000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByText('incorrect authentication code')).toBeInTheDocument()
    expect(screen.getByLabelText('2FA code')).toBeInTheDocument()

    // right code: password is re-sent alongside it
    fireEvent.change(screen.getByLabelText('2FA code'), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/account'))

    const [, init] = fetchMock.mock.calls[2]
    expect(JSON.parse(init.body as string)).toMatchObject({
      email: 'a@b.com',
      password: 'password123',
      otp: '123456',
    })
  })

  it('Back returns to the credentials step and clears the code', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'enter the 6-digit code', otp_required: true }),
      })),
    )

    renderLogin()
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.com' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    fireEvent.change(await screen.findByLabelText('2FA code'), { target: { value: '12' } })
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))

    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.queryByLabelText('2FA code')).not.toBeInTheDocument()
  })
})
