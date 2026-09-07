import { useCallback, useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { ApiError, UNAUTHENTICATED_EVENT } from '../api/client'
import { getSession, sessionLogin, sessionLogout } from '../api/endpoints'
import type { SessionRole } from '../api/types'
import { SessionContext } from './sessionContext'
import type { SessionContextValue } from './sessionContext'
import { Button, InlineError, Spinner } from './ui'

type Status = 'checking' | 'ready'

/**
 * Decides whether the app or the login screen is shown, and publishes the
 * signed-in role to everything below.
 *
 * When the backend reports auth_enabled: false (no OWNER_PASSWORD set) this is
 * transparent: children render immediately with full edit rights.
 */
export function SessionGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('checking')
  const [authEnabled, setAuthEnabled] = useState(false)
  const [role, setRole] = useState<SessionRole | null>(null)

  useEffect(() => {
    let cancelled = false
    getSession()
      .then((me) => {
        if (cancelled) return
        setAuthEnabled(me.auth_enabled)
        setRole(me.role)
      })
      .catch(() => {
        // The gate is the one thing that must fail closed: if /me is
        // unreachable, ask for a password rather than showing a broken app.
        if (!cancelled) {
          setAuthEnabled(true)
          setRole(null)
        }
      })
      .finally(() => {
        if (!cancelled) setStatus('ready')
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Any API 401 (an expired cookie, say) drops us back to the login screen.
  useEffect(() => {
    function handleUnauthenticated() {
      setAuthEnabled(true)
      setRole(null)
    }
    window.addEventListener(UNAUTHENTICATED_EVENT, handleUnauthenticated)
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, handleUnauthenticated)
  }, [])

  const logout = useCallback(() => {
    sessionLogout()
      .catch(() => {
        // Cookie may already be gone; the local state below is what matters.
      })
      .finally(() => setRole(null))
  }, [])

  if (status === 'checking') {
    return (
      <div className="login-screen">
        <Spinner />
      </div>
    )
  }

  if (authEnabled && role === null) {
    return <LoginScreen onAuthenticated={setRole} />
  }

  const value: SessionContextValue = {
    authEnabled,
    role,
    canEdit: !authEnabled || role === 'owner',
    logout,
  }

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

function LoginScreen({ onAuthenticated }: { onAuthenticated: (role: SessionRole) => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!password || busy) return
    setBusy(true)
    setError('')
    try {
      const { role } = await sessionLogin(password)
      onAuthenticated(role)
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? err.message : 'Could not sign in. Try again.')
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1 className="login-title">Gridiron HQ</h1>
        <p className="field-hint">Enter the league password to continue.</p>
        <label className="field">
          <span className="field-label">Password</span>
          <input
            className="field-input"
            type="password"
            autoFocus
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <InlineError>{error}</InlineError>}
        <Button type="submit" variant="primary" busy={busy} disabled={!password}>
          Sign in
        </Button>
      </form>
    </div>
  )
}
