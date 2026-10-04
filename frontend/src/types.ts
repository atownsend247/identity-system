export interface User {
  id: string
  email: string
  name: string
  totp_enabled: boolean
  // Drives which admin links show - every admin route still enforces it.
  is_admin: boolean
}

export interface App {
  name: string
  url: string
  description: string
}

export interface AdminApp {
  id: number
  name: string
  url: string
  description: string
}

export type OidcScope = 'openid' | 'email' | 'profile'

export interface AdminOidcClient {
  client_id: string
  redirect_uris: string[]
  allowed_scopes: OidcScope[]
}

// Only ever returned once, from create and rotate-secret.
export interface IssuedOidcSecret {
  client_id: string
  client_secret: string
}

export interface AdminUser {
  id: string
  email: string
  name: string
  created_at: string | null
  last_login_at: string | null
  totp_enabled: boolean
}
