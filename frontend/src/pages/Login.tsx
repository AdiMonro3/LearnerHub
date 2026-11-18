import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useAuth } from '../auth'
import { Alert, AuthCard, Divider, GoogleButton } from '../components/AuthCard'

// Error codes the backend puts in /login?error=... after a failed Google sign-in.
const OAUTH_ERRORS: Record<string, string> = {
  google_not_configured: "Google sign-in isn't configured on the server yet.",
  google_access_denied: 'Google sign-in was cancelled.',
  invalid_state: 'That sign-in attempt expired. Please try again.',
  google_failed: 'Google sign-in failed. Please try again.',
  email_not_verified: "Your Google account's email address isn't verified.",
  account_conflict: 'This email is already linked to a different Google account.',
  account_disabled: 'This account has been disabled.',
  session_failed:
    "Signed in with Google, but the session cookie didn't stick. Open the app at http://localhost:5173 (not 127.0.0.1).",
}

export default function Login() {
  const { login } = useAuth()
  const [params] = useSearchParams()
  const oauthError = params.get('error')

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(
    oauthError ? (OAUTH_ERRORS[oauthError] ?? `Sign-in failed (${oauthError}).`) : null,
  )
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password) // <GuestOnly> redirects once the user is set
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed')
      setSubmitting(false)
    }
  }

  return (
    <AuthCard title="Welcome back" subtitle="Sign in to your account">
      <GoogleButton />
      <Divider>or sign in with email</Divider>

      <form onSubmit={onSubmit} className="form">
        {error && <Alert>{error}</Alert>}
        <label>
          Email
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <button className="btn btn-primary" type="submit" disabled={submitting}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>

      <p className="switch">
        No account yet? <Link to="/register">Create one</Link>
      </p>
    </AuthCard>
  )
}
