import { useState } from 'react'
import { api } from '../api.js'
import { Card, ErrorNote, rs, useLoad } from '../ui.jsx'

const Stat = ({ label, value, hint }) => (
  <Card>
    <div className="text-xs font-medium uppercase tracking-wide text-stone-500">{label}</div>
    <div className="mt-1 text-2xl font-bold">{value}</div>
    {hint && <div className="text-xs text-stone-500">{hint}</div>}
  </Card>
)

function Bars({ rows, labelKey, valueKey, format = (v) => v }) {
  const max = Math.max(1, ...rows.map((r) => r[valueKey]))
  if (!rows.length) return <div className="text-sm text-stone-500">Nothing yet.</div>
  return (
    <div className="space-y-2">
      {rows.map((r) => (
        <div key={r[labelKey]} className="text-sm">
          <div className="flex justify-between"><span className="truncate">{r[labelKey]}</span><span className="font-medium">{format(r[valueKey])}</span></div>
          <div className="mt-0.5 h-2 rounded-full bg-stone-100"><div className="h-2 rounded-full bg-crumb-500" style={{ width: `${(100 * r[valueKey]) / max}%` }} /></div>
        </div>
      ))}
    </div>
  )
}

function Meter({ label, used, limit, pct }) {
  const tone = pct >= 90 ? 'bg-red-600' : pct >= 60 ? 'bg-amber-500' : 'bg-emerald-600'
  return (
    <div className="text-xs">
      <div className="flex justify-between text-stone-600"><span>{label}</span><span>{used.toLocaleString()} / {limit.toLocaleString()} ({pct}%)</span></div>
      <div className="mt-0.5 h-1.5 rounded-full bg-stone-100"><div className={`h-1.5 rounded-full ${tone}`} style={{ width: `${Math.min(100, pct)}%` }} /></div>
    </div>
  )
}

export default function Analytics({ tick }) {
  const [days, setDays] = useState(7)
  const a = useLoad(() => api.analytics(days), [days], tick)
  const d = a.data
  const seconds = (ms) => (ms ? `${(ms / 1000).toFixed(1)} s` : '–')

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Analytics</h1>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="rounded-lg border border-stone-300 bg-white px-2 py-1.5 text-sm" aria-label="Window">
          <option value={1}>Today</option><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option>
        </select>
      </div>
      <ErrorNote error={a.error} />
      {d && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Orders today" value={d.today.orders} hint={d.date} />
            <Stat label="Revenue today" value={rs(d.today.revenue)} hint="cancelled orders excluded" />
            <Stat label="Needs a person" value={d.today.open_escalations} hint="open escalations" />
            <Stat label="Payments to check" value={d.today.awaiting_payment_check} hint="advance not verified" />
          </div>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Chats" value={d.bot.conversations} hint={`last ${d.window_days} day(s)`} />
            <Stat label="Chats that became orders" value={`${d.bot.conversion_rate}%`} hint={`${d.bot.converted} of ${d.bot.conversations}`} />
            <Stat label="Escalation rate" value={`${d.bot.escalation_rate}%`} hint={`${d.bot.escalated} chats`} />
            <Stat label="Bot reply time" value={seconds(d.bot.avg_response_ms)} hint={`p90 ${seconds(d.bot.p90_response_ms)} · ${d.bot.answers} replies`} />
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            <Card><h3 className="mb-3 font-semibold">Top items</h3><Bars rows={d.top_items} labelKey="item" valueKey="qty" /></Card>
            <Card><h3 className="mb-3 font-semibold">Orders by area</h3><Bars rows={d.orders_by_area} labelKey="area" valueKey="orders" /></Card>
          </div>
          <Card>
            <h3 className="mb-1 font-semibold">AI model usage vs free-tier limits</h3>
            <p className="mb-3 text-xs text-stone-500">Both providers are free tiers; the bot switches to the second one when the first is rate-limited.</p>
            {Object.keys(d.llm).length === 0 && <div className="text-sm text-stone-500">No model calls recorded yet.</div>}
            <div className="grid gap-4 lg:grid-cols-2">
              {Object.entries(d.llm).map(([name, u]) => (
                <div key={name} className="space-y-2 rounded-xl bg-stone-50 p-3">
                  <div className="flex items-center justify-between"><span className="font-semibold capitalize">{name}</span><span className="text-xs text-stone-500">{u.today.requests} requests · {u.today.errors} errors · {u.today.served_as_fallback} as fallback</span></div>
                  {u.limits.tokens_per_day && <Meter label="Tokens today" used={u.today.input_tokens + u.today.output_tokens} limit={u.limits.tokens_per_day} pct={u.percent_of_limit.tokens_per_day ?? 0} />}
                  {u.limits.requests_per_day && <Meter label="Requests today" used={u.today.requests} limit={u.limits.requests_per_day} pct={u.percent_of_limit.requests_per_day ?? 0} />}
                  {u.last_error && <div className="text-xs text-amber-700">Last error: {u.last_error}</div>}
                </div>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
