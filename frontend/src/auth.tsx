import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { api, refreshSession, type User } from './api'

type AuthContextValue = {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, fullName?: string) => Promise<void>
  logout: () => Promise<void>
  reloadUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  // Restore the session from the refresh cookie on page load (also how a
  // Google sign-in lands: the backend set the cookie, then redirected here).
  useEffect(() => {
    refreshSession()
      .then((session) => setUser(session?.user ?? null))
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    setUser((await api.login(email, password)).user)
  }, [])

  const register = useCallback(async (email: string, password: string, fullName?: string) => {
    setUser((await api.register({ email, password, full_name: fullName })).user)
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      setUser(null)
    }
  }, [])

  const reloadUser = useCallback(async () => {
    setUser(await api.me())
  }, [])

  const value = useMemo(
    () => ({ user, loading, login, register, logout, reloadUser }),
    [user, loading, login, register, logout, reloadUser],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

export function FullPageSpinner() {
  return (
    <div className="center-screen" aria-busy="true" aria-label="Loading">
      <div className="spinner" />
    </div>
  )
}

/** Only signed-in users. Others go to /login and come back afterwards. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <FullPageSpinner />
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return children
}

/** Only signed-out users (login/register pages). Once signed in, sends them on
 *  to the page they originally asked for, or the dashboard. */
export function GuestOnly({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <FullPageSpinner />
  if (user) {
    const from = (location.state as { from?: string } | null)?.from
    return <Navigate to={from && from.startsWith('/') ? from : '/dashboard'} replace />
  }
  return children
}
