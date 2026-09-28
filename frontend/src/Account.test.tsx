import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import Account from './Account'
import type { User } from './types'

function renderAccount(user: User | null) {
  return render(
    <MemoryRouter initialEntries={['/account']}>
      <Routes>
        <Route path="/account" element={<Account user={user} />} />
        <Route path="/login" element={<p>Login page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Account', () => {
  it('redirects to /login?rd=/account when signed out', () => {
    renderAccount(null)
    expect(screen.getByText('Login page')).toBeInTheDocument()
  })

  it("shows the signed-in user's details", () => {
    renderAccount({ id: '1', email: 'a@b.com', name: 'A B', totp_enabled: true })

    expect(screen.getByText('A B')).toBeInTheDocument()
    expect(screen.getByText('a@b.com')).toBeInTheDocument()
    expect(screen.getByText('Enabled')).toBeInTheDocument()
  })
})
