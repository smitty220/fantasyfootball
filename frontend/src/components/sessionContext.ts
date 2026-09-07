import { createContext, useContext } from 'react'
import type { SessionRole } from '../api/types'

export interface SessionContextValue {
  /** False when the backend has no OWNER_PASSWORD set: everything is open. */
  authEnabled: boolean
  role: SessionRole | null
  /** True for the owner, and for everyone when the gate is off. */
  canEdit: boolean
  logout: () => void
}

export const SessionContext = createContext<SessionContextValue | null>(null)

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error('useSession must be used within SessionProvider')
  return ctx
}
