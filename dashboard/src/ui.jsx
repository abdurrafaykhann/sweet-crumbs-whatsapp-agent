import { useEffect, useState } from 'react'

export const rs = (n) => `Rs ${Number(n || 0).toLocaleString('en-PK')}`

export const when = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d)) return iso
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}
export const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '')

const TONES = {
  gray: 'bg-stone-100 text-stone-700 ring-stone-200',
  amber: 'bg-amber-50 text-amber-800 ring-amber-200',
  green: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  red: 'bg-red-50 text-red-800 ring-red-200',
  blue: 'bg-sky-50 text-sky-800 ring-sky-200',
  purple: 'bg-violet-50 text-violet-800 ring-violet-200',
}
export const STATUS_TONE = {
  draft: 'gray', awaiting_advance: 'amber', confirmed: 'blue', scheduled: 'purple', preparing: 'amber',
  ready_for_pickup: 'green', out_for_delivery: 'blue', delivered: 'green', cancelled: 'red', refund_processing: 'red',
}

export function Badge({ tone = 'gray', children, className = '' }) {
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]} ${className}`}>{children}</span>
}

export const StatusBadge = ({ status, label }) => <Badge tone={STATUS_TONE[status] || 'gray'}>{label || status}</Badge>

export function Button({ children, variant = 'primary', className = '', ...rest }) {
  const styles = {
    primary: 'bg-crumb-600 text-white hover:bg-crumb-700 disabled:opacity-50',
    soft: 'bg-crumb-100 text-crumb-700 hover:bg-orange-200 disabled:opacity-50',
    ghost: 'bg-white text-stone-700 ring-1 ring-stone-300 hover:bg-stone-50 disabled:opacity-50',
    danger: 'bg-white text-red-700 ring-1 ring-red-300 hover:bg-red-50 disabled:opacity-50',
  }
  return (
    <button className={`inline-flex min-h-10 items-center justify-center gap-1 rounded-lg px-3 py-2 text-sm font-medium transition ${styles[variant]} ${className}`} {...rest}>
      {children}
    </button>
  )
}

export const Card = ({ children, className = '' }) => <div className={`rounded-2xl bg-white p-4 shadow-sm ring-1 ring-stone-200 ${className}`}>{children}</div>

export const Field = ({ label, children }) => (
  <label className="block text-sm">
    <span className="mb-1 block font-medium text-stone-600">{label}</span>
    {children}
  </label>
)
export const inputCls = 'w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm outline-none focus:border-crumb-500 focus:ring-2 focus:ring-orange-200'

export const Empty = ({ children }) => <div className="rounded-2xl border border-dashed border-stone-300 p-8 text-center text-sm text-stone-500">{children}</div>

export function Drawer({ open, onClose, title, children }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onClick={onClose}>
      <div className="pop flex h-full w-full max-w-xl flex-col bg-stone-50 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-stone-200 bg-white px-4 py-3">
          <h2 className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-lg px-2 py-1 text-xl text-stone-500 hover:bg-stone-100">×</button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  )
}

// Small hook: load data now, again when `deps` change, and again whenever the app-wide live tick moves.
export function useLoad(fn, deps, tick = 0) {
  const [state, set] = useState({ data: null, error: null, loading: true })
  useEffect(() => {
    let live = true
    fn().then((data) => live && set({ data, error: null, loading: false }))
      .catch((error) => live && set((s) => ({ data: s.data, error, loading: false })))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])
  return state
}

export function Toasts({ items, dismiss }) {
  return (
    <div className="pointer-events-none fixed bottom-16 right-3 z-50 md:bottom-4 flex w-[min(92vw,22rem)] flex-col gap-2">
      {items.map((t) => (
        <button key={t.id} onClick={() => dismiss(t)} className="pop pointer-events-auto rounded-xl bg-crumb-900 px-4 py-3 text-left text-sm text-white shadow-lg">
          <div className="font-semibold">{t.title}</div>
          <div className="text-orange-100">{t.body}</div>
        </button>
      ))}
    </div>
  )
}

export function ErrorNote({ error }) {
  if (!error) return null
  return <div className="rounded-lg bg-red-50 p-3 text-sm text-red-800 ring-1 ring-red-200">{error.message}</div>
}

// hash routes: #/orders, #/orders/SC-10432, #/inbox/923001234567
export const go = (page, arg = '') => { window.location.hash = `#/${page}${arg ? '/' + encodeURIComponent(arg) : ''}` }
