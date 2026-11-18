import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { useAuth } from '../auth'
import { Alert, AuthCard, Divider, GoogleButton } from '../components/AuthCard'

const MIN_PASSWORD = 8

export default function Register() {
  const { register } = useAuth()
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (password.length < MIN_PASSWORD) return setError(`Password must be at least ${MIN_PASSWORD} characters.`)
    if (password !== confirm) return setError("Passwords don't match.")

    setSubmitting(true)
    try {
      await register(email, password, fullName.trim() || undefined)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-up failed')
      setSubmitting(false)
    }
  }

  return (
    <AuthCard title="Create your account" subtitle="Use Google, or sign up with an email and password">
      <GoogleButton label="Sign up with Google" />
      <Divider>or create an account</Divider>

      <form onSubmit={onSubmit} className="form">
        {error && <Alert>{error}</Alert>}
        <label>
          <span>
            Full name <span className="optional">(optional)</span>
          </span>
          <input autoComplete="name" value={fullName} onChange={(e) => setFullName(e.target.value)} maxLength={200} />
        </label>
        <label>
          Email
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <div className="field">
          <label htmlFor="new-password">Password</label>
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD}
            maxLength={128}
            aria-describedby="password-hint"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <small id="password-hint" className="muted">
            At least {MIN_PASSWORD} characters
          </small>
        </div>
        <label>
          Confirm password
          <input
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        <button className="btn btn-primary" type="submit" disabled={submitting}>
          {submitting ? 'Creating account…' : 'Create account'}
        </button>
      </form>

      <p className="switch">
        Already have an account? <Link to="/login">Sign in</Link>
      </p>
    </AuthCard>
  )
}
