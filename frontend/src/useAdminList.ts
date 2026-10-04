import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from './api'

export type AdminStatus = 'loading' | 'ok' | 'unauthenticated' | 'forbidden' | 'error'

// Loads an admin-only list and maps the backend's 401/403 onto page states,
// so every admin page sends a signed-out visitor to /login and shows a
// forbidden message to a non-admin the same way (see Admin.tsx).
export function useAdminList<T>(path: string) {
  const [items, setItems] = useState<T[]>([])
  const [status, setStatus] = useState<AdminStatus>('loading')

  const refresh = useCallback(async () => {
    try {
      setItems(await api.get<T[]>(path))
      setStatus('ok')
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setStatus('unauthenticated')
      else if (err instanceof ApiError && err.status === 403) setStatus('forbidden')
      else setStatus('error')
    }
  }, [path])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { items, status, refresh }
}

export function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Something went wrong'
}
