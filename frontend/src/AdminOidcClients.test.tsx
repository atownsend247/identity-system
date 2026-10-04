import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AdminOidcClients from './AdminOidcClients'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/oidc-clients']}>
      <Routes>
        <Route path="/admin/oidc-clients" element={<AdminOidcClients />} />
        <Route path="/login" element={<p>Login page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const jenkins = {
  client_id: 'jenkins',
  redirect_uris: ['https://jenkins.example.com/securityRealm/finishLogin'],
  allowed_scopes: ['openid', 'email', 'profile'],
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('AdminOidcClients', () => {
  it('redirects to /login when signed out', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 401, json: async () => ({}) })))
    renderPage()
    expect(await screen.findByText('Login page')).toBeInTheDocument()
  })

  it('shows a forbidden message for a signed-in non-admin', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 403, json: async () => ({ detail: 'forbidden' }) })),
    )
    renderPage()
    expect(await screen.findByText(/Forbidden/)).toBeInTheDocument()
  })

  it('lists registered clients without any secret material', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => [jenkins] })),
    )
    renderPage()
    expect(await screen.findByText('jenkins')).toBeInTheDocument()
    expect(screen.getByText('https://jenkins.example.com/securityRealm/finishLogin')).toBeInTheDocument()
    expect(screen.queryByText(/client_secret/)).not.toBeInTheDocument()
  })

  it('shows a newly issued secret once, after registering a client', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method === 'POST') {
          return {
            ok: true,
            status: 201,
            json: async () => ({
              client_id: 'wiki',
              redirect_uris: ['https://wiki.example.com/cb'],
              allowed_scopes: ['openid'],
              client_secret: 'plaintext-once-xyz',
            }),
          }
        }
        return { ok: true, status: 200, json: async () => [] }
      }),
    )

    renderPage()
    await screen.findByText('No OIDC clients registered yet.')
    fireEvent.change(screen.getByLabelText('Client ID'), { target: { value: 'wiki' } })
    fireEvent.change(screen.getByLabelText(/Redirect URIs/), {
      target: { value: '  https://wiki.example.com/cb  \n\n' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Register client' }))

    expect(await screen.findByText('plaintext-once-xyz')).toBeInTheDocument()
    expect(screen.getByText(/won't be shown again/)).toBeInTheDocument()
  })

  it('sends the parsed redirect URIs and selected scopes on create', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        return {
          ok: true,
          status: 201,
          json: async () => ({ client_id: 'wiki', redirect_uris: [], allowed_scopes: [], client_secret: 's' }),
        }
      }
      return { ok: true, status: 200, json: async () => [] }
    })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await screen.findByText('No OIDC clients registered yet.')
    fireEvent.change(screen.getByLabelText('Client ID'), { target: { value: 'wiki' } })
    fireEvent.change(screen.getByLabelText(/Redirect URIs/), {
      target: { value: 'https://a.example.com/cb\n  https://b.example.com/cb  ' },
    })
    fireEvent.click(screen.getByLabelText('email'))
    fireEvent.click(screen.getByRole('button', { name: 'Register client' }))

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
      expect(post).toBeDefined()
      expect(JSON.parse(post![1]!.body as string)).toEqual({
        client_id: 'wiki',
        redirect_uris: ['https://a.example.com/cb', 'https://b.example.com/cb'],
        allowed_scopes: ['openid', 'email'],
      })
    })
  })

  it('rotates a secret only after confirmation', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/rotate-secret') && init?.method === 'POST') {
        return { ok: true, status: 200, json: async () => ({ client_id: 'jenkins', client_secret: 'rotated-secret' }) }
      }
      return { ok: true, status: 200, json: async () => [jenkins] }
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()
    await screen.findByText('jenkins')
    fireEvent.click(screen.getByRole('button', { name: 'Rotate secret' }))

    expect(await screen.findByText('rotated-secret')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/rotate-secret'))).toBe(true)
  })

  it('does not rotate when the confirmation is declined', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
      ok: true,
      status: 200,
      json: async () => [jenkins],
    }))
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'confirm').mockReturnValue(false)

    renderPage()
    await screen.findByText('jenkins')
    fireEvent.click(screen.getByRole('button', { name: 'Rotate secret' }))

    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/rotate-secret'))).toBe(false)
  })
})
