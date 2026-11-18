import { Navigate } from 'react-router'
import { FullPageSpinner, useAuth } from '../auth'

/** Google sign-in lands here. The backend has already set the refresh cookie;
 *  <AuthProvider> picks it up on load, so we just wait for that and move on. */
export default function AuthCallback() {
  const { user, loading } = useAuth()
  if (loading) return <FullPageSpinner />
  return <Navigate to={user ? '/dashboard' : '/login?error=session_failed'} replace />
}
