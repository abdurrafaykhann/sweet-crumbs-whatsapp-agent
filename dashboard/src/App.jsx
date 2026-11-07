import { useCallback, useEffect, useRef, useState } from 'react'
import { api, setToken, setUnauthorizedHandler, token } from './api.js'
import { Toasts, go } from './ui.jsx'
import Login from './pages/Login.jsx'
import Orders from './pages/Orders.jsx'
import Inbox from './pages/Inbox.jsx'
import Escalations from './pages/Escalations.jsx'
import Menu from './pages/Menu.jsx'
import Customers from './pages/Customers.jsx'
import Analytics from './pages/Analytics.jsx'

const TABS = [
  { id: 'orders', label: 'Orders', icon: '🧾' },
  { id: 'inbox', label: 'Inbox', icon: '💬' },
  { id: 'escalations', label: 'Needs you', icon: '🚨' },
  { id: 'menu', label: 'Menu & stock', icon: '🍰' },
  { id: 'customers', label: 'Customers', icon: '👥' },
  { id: 'analytics', label: 'Analytics', icon: '📈' },
]

const parseHash = () => {
  const [, page = 'orders', arg = ''] = window.location.hash.split('/')
  return { page: TABS.some((t) => t.id === page) ? page : 'orders', arg: decodeURIComponent(arg) }
}


let audio
function beep(kind) {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)()
    const notes = kind === 'escalation' ? [880, 660, 880] : [660, 880]
    notes.forEach((f, i) => {
      const o = audio.createOscillator(); const g = audio.createGain()
      o.frequency.value = f; o.type = 'sine'; o.connect(g); g.connect(audio.destination)
      const t = audio.currentTime + i * 0.16
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.14)
      o.start(t); o.stop(t + 0.15)
    })
  } catch { /* audio not available */ }
}

export default function App() {
  const [authed, setAuthed] = useState(!!token())
  const [route, setRoute] = useState(parseHash())
  const [tick, setTick] = useState(0)
  const [toasts, setToasts] = useState([])
  const [counts, setCounts] = useState({ escalations: 0, orders: 0 })
  const [sound, setSound] = useState(() => { try { return localStorage.getItem('sc_sound') !== 'off' } catch { return true } })
  const soundRef = useRef(sound)
  soundRef.current = sound

  const logout = useCallback(() => { setToken(null); setAuthed(false) }, [])
  useEffect(() => { setUnauthorizedHandler(logout) }, [logout])
  useEffect(() => {
    const on = () => setRoute(parseHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const toast = useCallback((title, body, ttl = 7000) => {
    const id = Math.random()
    setToasts((t) => [...t.slice(-3), { id, title, body }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), ttl)
  }, [])

  // Live updates: poll /updates every 4 seconds. Starts from "now" so a refresh does not replay old orders.
  useEffect(() => {
    if (!authed) return undefined
    let stop = false
    let marks = null
    const refreshCounts = () => Promise.all([api.escalations('open'), api.orders({ status: 'awaiting_advance', limit: 1 })])
      .then(([e, o]) => !stop && setCounts({ escalations: e.length, orders: o.total })).catch(() => {})
    refreshCounts()
    const step = async () => {
      try {
        const u = await api.updates(marks || {})
        if (stop) return
        if (marks) {
          let changed = false
          u.orders.forEach((o) => { changed = true; toast(`New order ${o.code}`, `${o.customer} · Rs ${o.total.toLocaleString('en-PK')} · ${o.fulfilment}`); if (soundRef.current) beep('order') })
          u.escalations.forEach((e) => { changed = true; toast('Customer needs a person', `${e.phone} · ${e.reason}`); if (soundRef.current) beep('escalation') })
          if (u.messages.length) changed = true
          if (changed) { setTick((n) => n + 1); refreshCounts() }
        }
        marks = u.watermarks
      } catch { /* offline: try again next time */ }
    }
    step()
    const id = setInterval(step, 4000)
    return () => { stop = true; clearInterval(id) }
  }, [authed, toast])

  if (!authed) return <Login onDone={() => setAuthed(true)} />

  const page = route.page
  const badge = { escalations: counts.escalations, orders: counts.orders }
  const props = { tick, arg: route.arg, toast }

  const demo = (() => { try { return localStorage.getItem('sc_role') === 'demo' } catch { return false } })()
  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-stone-200 bg-white md:flex">
        <div className="px-5 py-5">
          <div className="text-lg font-bold text-crumb-700">🧁 Sweet Crumbs</div>
          <div className="text-xs text-stone-500">Owner dashboard</div>
        </div>
        <nav className="flex-1 space-y-1 px-3">
          {TABS.map((t) => (
            <button key={t.id} onClick={() => go(t.id)} className={`flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-left text-sm font-medium ${page === t.id ? 'bg-crumb-100 text-crumb-700' : 'text-stone-600 hover:bg-stone-100'}`}>
              <span>{t.icon} {t.label}</span>
              {t.id === 'escalations' && badge.escalations > 0 && <span className="rounded-full bg-red-600 px-2 text-xs text-white">{badge.escalations}</span>}
              {t.id === 'orders' && badge.orders > 0 && <span title="Waiting for an advance check" className="rounded-full bg-amber-500 px-2 text-xs text-white">{badge.orders}</span>}
            </button>
          ))}
        </nav>
        <div className="space-y-1 border-t border-stone-200 p-3 text-sm">
          <button onClick={() => { const v = !sound; setSound(v); try { localStorage.setItem('sc_sound', v ? 'on' : 'off') } catch { /* ignore */ } if (v) beep('order') }} className="w-full rounded-lg px-3 py-2 text-left text-stone-600 hover:bg-stone-100">{sound ? '🔔 Sound on' : '🔕 Sound off'}</button>
          <button onClick={logout} className="w-full rounded-lg px-3 py-2 text-left text-stone-600 hover:bg-stone-100">Log out</button>
        </div>
      </aside>

      <header className="flex items-center justify-between border-b border-stone-200 bg-white px-4 py-2 md:hidden">
        <div className="font-bold text-crumb-700">🧁 Sweet Crumbs</div>
        <div className="flex gap-1 text-sm">
          <button onClick={() => { const v = !sound; setSound(v); try { localStorage.setItem('sc_sound', v ? 'on' : 'off') } catch { /* ignore */ } }} className="rounded-lg px-2 py-1" aria-label="Toggle sound">{sound ? '🔔' : '🔕'}</button>
          <button onClick={logout} className="rounded-lg px-2 py-1 text-stone-600">Log out</button>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto">
        {demo && <div className="bg-amber-100 px-4 py-1.5 text-center text-xs font-medium text-amber-900">Demo login: read-only. You can look around, but changes are switched off.</div>}
        <div className="mx-auto max-w-6xl p-3 md:p-6">
          {page === 'orders' && <Orders {...props} />}
          {page === 'inbox' && <Inbox {...props} />}
          {page === 'escalations' && <Escalations {...props} />}
          {page === 'menu' && <Menu {...props} />}
          {page === 'customers' && <Customers {...props} />}
          {page === 'analytics' && <Analytics {...props} />}
        </div>
      </main>

      <nav className="grid grid-cols-6 border-t border-stone-200 bg-white md:hidden">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => go(t.id)} className={`relative flex flex-col items-center py-2 text-[10px] ${page === t.id ? 'font-semibold text-crumb-700' : 'text-stone-500'}`}>
            <span className="text-lg leading-none">{t.icon}</span>
            <span className="mt-0.5 truncate">{t.label.split(' ')[0]}</span>
            {t.id === 'escalations' && badge.escalations > 0 && <span className="absolute right-3 top-1 rounded-full bg-red-600 px-1.5 text-[10px] text-white">{badge.escalations}</span>}
          </button>
        ))}
      </nav>
      <Toasts items={toasts} dismiss={(t) => setToasts((x) => x.filter((y) => y.id !== t.id))} />
    </div>
  )
}
