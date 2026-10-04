import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AdminApps from './AdminApps'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/apps']}>
      <Routes>
        <Route path="/admin/apps" element={<AdminApps />} />
        <Route path="/login" element={<p>Login page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const existingApp = {
  id: 7,
  name: 'Finance',
  url: 'https://finance.example.com',
  description: 'Accounts',
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('AdminApps', () => {
  it('redirects to /login?rd=/admin/apps when signed out', async () => {
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

  it('lists the configured apps for an admin', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => [existingApp] })),
    )
    renderPage()
    expect(await screen.findByText('Finance')).toBeInTheDocument()
    expect(screen.getByText('Accounts')).toBeInTheDocument()
  })

  it('adds a new app with a POST and reloads the list', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        return { ok: true, status: 201, json: async () => ({ id: 8 }) }
      }
      return { ok: true, status: 200, json: async () => [] }
    })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await screen.findByText('No apps yet.')

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Wiki' } })
    fireEvent.change(screen.getByLabelText('URL'), {
      target: { value: 'https://wiki.example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add app' }))

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
      expect(post).toBeDefined()
      expect(JSON.parse(post![1]!.body as string)).toEqual({
        name: 'Wiki',
        url: 'https://wiki.example.com',
        description: '',
      })
    })
  })

  it('shows the server error when a save is rejected', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method === 'POST') {
          return { ok: false, status: 422, statusText: 'Unprocessable', json: async () => ({ detail: 'url must be http(s)' }) }
        }
        return { ok: true, status: 200, json: async () => [] }
      }),
    )

    renderPage()
    await screen.findByText('No apps yet.')
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Bad' } })
    fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://bad.example.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add app' }))

    expect(await screen.findByText('url must be http(s)')).toBeInTheDocument()
  })
})
