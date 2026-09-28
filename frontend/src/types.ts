export interface User {
  id: string
  email: string
  name: string
  totp_enabled: boolean
}

export interface App {
  name: string
  url: string
  description: string
}

export interface AdminUser {
  id: string
  email: string
  name: string
  created_at: string | null
  last_login_at: string | null
  totp_enabled: boolean
}
