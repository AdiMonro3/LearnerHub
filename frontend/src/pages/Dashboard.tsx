import { useState, type FormEvent } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import { Alert } from '../components/AuthCard'

export default function Dashboard() {
  const { user, logout } = useAuth()
  if (!user) return null // <RequireAuth> guarantees a user; this satisfies TypeScript

  const name = user.full_name || user.email.split('@')[0]
  const initials = name
    .split(/\s+/)
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

  return (
    <main className="dashboard">
      <header className="topbar">
        <strong>Dashboard</strong>
        <button className="btn btn-ghost" onClick={logout}>
          Sign out
        </button>
      </header>

      <section className="card profile">
        {user.avatar_url ? (
          <img className="avatar" src={user.avatar_url} alt="" referrerPolicy="no-referrer" />
        ) : (
          <div className="avatar avatar-fallback" aria-hidden="true">
            {initials}
          </div>
        )}
        <div>
          <h1>Hi, {name}</h1>
          <p className="muted">{user.email}</p>
          <div className="badges">
            <Badge on={user.google_linked}>Google linked</Badge>
            <Badge on={user.has_password}>Password set</Badge>
            <Badge on={user.email_verified}>Email verified</Badge>
          </div>
          <p className="muted small">Member since {new Date(user.created_at).toLocaleDateString()}</p>
        </div>
      </section>

      <ProtectedApiDemo />
      <PasswordForm hasPassword={user.has_password} />
    </main>
  )
}

function Badge({ on, children }: { on: boolean; children: string }) {
  return (
    <span className={`badge ${on ? 'badge-on' : 'badge-off'}`}>
      {on ? '✓' : '–'} {children}
    </span>
  )
}

function ProtectedApiDemo() {
  const [result, setResult] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function callApi() {
    setLoading(true)
    try {
      const me = await api.me()
      setResult(JSON.stringify(me, null, 2))
    } catch (err) {
      setResult(`Error: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setLoading(false)
    }
  }

  const token = api.currentAccessToken()
  return (
    <section className="card">
      <h2>Call a protected endpoint</h2>
      <p className="muted">
        Sends <code>GET /users/me</code> with <code>Authorization: Bearer &lt;access token&gt;</code>. Expired tokens are
        refreshed automatically.
      </p>
      {token && (
        <p className="token" title="Your current access token (kept in memory only)">
          {token.slice(0, 24)}…{token.slice(-8)}
        </p>
      )}
      <button className="btn btn-secondary" onClick={callApi} disabled={loading}>
        {loading ? 'Calling…' : 'Call /users/me'}
      </button>
      {result && <pre className="output">{result}</pre>}
    </section>
  )
}

function PasswordForm({ hasPassword }: { hasPassword: boolean }) {
  const { reloadUser } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [message, setMessage] = useState<{ kind: 'error' | 'success'; text: string } | null>(null)
  const [saving, setSaving] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setMessage(null)
    setSaving(true)
    try {
      await api.changePassword({ current_password: hasPassword ? current : undefined, new_password: next })
      setCurrent('')
      setNext('')
      setMessage({
        kind: 'success',
        text: hasPassword
          ? 'Password changed. Your other devices have been signed out.'
          : 'Password set. You can now also sign in with your email and password.',
      })
      await reloadUser()
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof Error ? err.message : 'Could not update password' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="card">
      <h2>{hasPassword ? 'Change password' : 'Set a password'}</h2>
      {!hasPassword && (
        <p className="muted">You signed up with Google. Add a password to also sign in with your email.</p>
      )}
      <form onSubmit={onSubmit} className="form">
        {message && <Alert kind={message.kind}>{message.text}</Alert>}
        {hasPassword && (
          <label>
            Current password
            <input
              type="password"
              autoComplete="current-password"
              required
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </label>
        )}
        <label>
          New password
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            maxLength={128}
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </label>
        <button className="btn btn-primary" type="submit" disabled={saving}>
          {saving ? 'Saving…' : hasPassword ? 'Change password' : 'Set password'}
        </button>
      </form>
    </section>
  )
}
