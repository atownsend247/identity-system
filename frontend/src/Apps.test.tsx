import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import Apps from './Apps'

describe('Apps', () => {
  it('renders a link for each configured app', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => [
          { name: 'Finance', url: 'https://finance.example.com', description: 'Money stuff' },
        ],
      })),
    )

    render(<Apps />)

    const link = await screen.findByRole('link', { name: /Finance/ })
    expect(link).toHaveAttribute('href', 'https://finance.example.com')
    expect(screen.getByText('Money stuff')).toBeInTheDocument()
  })

  it('shows a message when no apps are configured', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => [] })))

    render(<Apps />)

    expect(await screen.findByText('No apps configured yet.')).toBeInTheDocument()
  })
})
