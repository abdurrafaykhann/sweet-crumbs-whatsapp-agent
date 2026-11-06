import { useState } from 'react'
import { api, setToken } from '../api.js'
import { Button, ErrorNote, inputCls } from '../ui.jsx'

export default function Login({ onDone }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const { token } = await api.login(password)
      setToken(token)
      onDone()
    } catch (err) {
      setError(err.status === 503 ? new Error('Login is switched off: set ADMIN_PASSWORD and JWT_SECRET (16+ characters) in .env.') :
        err.status === 429 ? new Error('Too many attempts. Wait a minute.') : new Error('Wrong password.'))
    } finally { setBusy(false) }
  }

  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl bg-white p-6 shadow-sm ring-1 ring-stone-200">
        <div className="text-center">
          <div className="text-4xl">🧁</div>
          <h1 className="mt-2 text-xl font-bold text-crumb-700">Sweet Crumbs</h1>
          <p className="text-sm text-stone-500">Owner dashboard</p>
        </div>
        <label className="block text-sm font-medium text-stone-600">Password
          <input autoFocus type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={`${inputCls} mt-1`} autoComplete="current-password" />
        </label>
        <ErrorNote error={error} />
        <Button type="submit" disabled={busy || !password} className="w-full">{busy ? 'Checking…' : 'Log in'}</Button>
      </form>
    </div>
  )
}
