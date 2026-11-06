import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { Badge, Button, Card, Empty, ErrorNote, StatusBadge, clock, go, inputCls, rs, useLoad, when } from '../ui.jsx'

export default function Inbox({ tick, arg, toast }) {
  const [q, setQ] = useState('')
  const [only, setOnly] = useState(false)
  const list = useLoad(() => api.conversations({ q, escalated: only ? true : undefined }), [q, only], tick)
  const rows = list.data || []

  return (
    <div className="grid gap-4 md:grid-cols-[20rem_1fr]">
      <div className={`space-y-3 ${arg ? 'hidden md:block' : ''}`}>
        <h1 className="text-xl font-bold">Inbox</h1>
        <input placeholder="Search name, phone, text" value={q} onChange={(e) => setQ(e.target.value)} className={inputCls} />
        <label className="flex items-center gap-2 text-sm text-stone-600"><input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} /> Only chats that need a person</label>
        <ErrorNote error={list.error} />
        {list.data && rows.length === 0 && <Empty>No conversations.</Empty>}
        <div className="space-y-1.5">
          {rows.map((c) => (
            <button key={c.phone} onClick={() => go('inbox', c.phone)} className={`block w-full rounded-xl p-3 text-left ring-1 ${arg === c.phone ? 'bg-crumb-50 ring-crumb-500' : 'bg-white ring-stone-200 hover:ring-stone-400'}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{c.name || c.phone}</span>
                <span className="shrink-0 text-xs text-stone-500">{when(c.last_at)}</span>
              </div>
              <div className="rtl-text mt-0.5 truncate text-sm text-stone-600">{c.last_role === 'customer' ? '' : '↩ '}{c.last_text}</div>
              <div className="mt-1 flex gap-1">
                {c.open_escalation && <Badge tone="red">{c.open_escalation.reason}</Badge>}
                {!c.bot_active && <Badge tone="amber">staff</Badge>}
              </div>
            </button>
          ))}
        </div>
      </div>
      <div className={arg ? '' : 'hidden md:block'}>
        {arg ? <Thread phone={arg} tick={tick} toast={toast} /> : <Empty>Pick a conversation.</Empty>}
      </div>
    </div>
  )
}

function Thread({ phone, tick, toast }) {
  const [rev, setRev] = useState(0)
  const t = useLoad(() => api.conversation(phone), [phone, rev], tick)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const end = useRef(null)
  const c = t.data

  useEffect(() => { setText(''); setError(null) }, [phone])
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [c?.messages?.length, phone])

  const act = async (fn, msg) => {
    setBusy(true); setError(null)
    try { await fn(); toast(msg, '', 3000); setRev((n) => n + 1) } catch (e) { setError(e) } finally { setBusy(false) }
  }
  const send = async (e) => {
    e.preventDefault()
    if (!text.trim()) return
    await act(() => api.reply(phone, text), 'Sent')
    setText('')
  }

  if (!c) return <div className="text-sm text-stone-500">{t.error ? <ErrorNote error={t.error} /> : 'Loading…'}</div>
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <button className="rounded-lg px-2 py-1 text-sm text-crumb-600 md:hidden" onClick={() => go('inbox')}>← Back</button>
        <div>
          <div className="font-semibold">{c.customer?.name || phone}</div>
          <div className="text-xs text-stone-500">{phone}{c.customer ? ` · ${c.customer.language} · ${c.customer.points} points` : ' · new customer'}</div>
        </div>
        <div className="ml-auto flex gap-2">
          {c.bot_active
            ? <Button variant="ghost" disabled={busy} onClick={() => act(() => api.takeOver(phone), 'You have this chat — the bot is silent')}>Take over</Button>
            : <Button disabled={busy} onClick={() => act(() => api.handBack(phone), 'Handed back to the bot')}>Hand back to bot</Button>}
        </div>
      </div>

      {c.escalations.map((e) => (
        <Card key={e.id} className="border-red-200 bg-red-50 text-sm">
          <div className="flex items-center gap-2"><Badge tone="red">{e.reason}</Badge><Badge tone={e.priority === 'high' ? 'red' : 'gray'}>{e.priority}</Badge></div>
          <p className="mt-1 text-red-900">{e.summary}</p>
          {e.collected && <p className="mt-1 text-red-900">Collected: {e.collected.items?.join(', ')}{e.collected.total ? ` · ${rs(e.collected.total)}` : ''}</p>}
          <div className="mt-2"><Button variant="ghost" disabled={busy} onClick={() => act(() => api.resolve(e.id, false), 'Resolved')}>Mark resolved</Button></div>
        </Card>
      ))}

      <div className="max-h-[55vh] space-y-2 overflow-y-auto rounded-2xl bg-[#efeae2] p-3">
        {c.messages.map((m) => <Bubble key={m.id} m={m} />)}
        <div ref={end} />
      </div>

      {!c.bot_active && (
        <form onSubmit={send} className="flex gap-2">
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Reply as the shop (the bot stays silent)" className={inputCls} />
          <Button type="submit" disabled={busy || !text.trim()}>Send</Button>
        </form>
      )}
      {c.bot_active && <p className="text-xs text-stone-500">The bot is answering this chat. Take over to reply yourself.</p>}
      <ErrorNote error={error} />

      {(c.draft || c.orders.length > 0) && (
        <Card className="space-y-2 text-sm">
          {c.draft && (
            <div>
              <div className="font-medium">Order being put together</div>
              <div className="text-stone-600">{c.draft.lines?.map((l) => `${l.qty} × ${l.name}`).join(', ')} · {rs(c.draft.total)}</div>
              {c.draft.missing?.length > 0 && <div className="text-amber-700">Still needed: {c.draft.missing.join(', ')}</div>}
            </div>
          )}
          {c.orders.length > 0 && (
            <div>
              <div className="font-medium">Orders</div>
              <div className="flex flex-wrap gap-2">
                {c.orders.map((o) => <button key={o.code} onClick={() => go('orders', o.code)} className="rounded-lg bg-stone-100 px-2 py-1 hover:bg-stone-200">{o.code} <StatusBadge status={o.status} label={o.status.replaceAll('_', ' ')} /> {rs(o.total)}</button>)}
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}

function Bubble({ m }) {
  if (m.role === 'note') return <div className="text-center text-xs text-stone-500">— {m.text} —</div>
  const mine = m.role !== 'customer'
  const tools = (m.tools || []).map((t) => t.name).filter(Boolean)
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm shadow-sm ${m.role === 'human' ? 'bg-sky-100' : mine ? 'bg-[#d9fdd3]' : 'bg-white'}`}>
        {m.role === 'human' && <div className="text-[10px] font-semibold uppercase text-sky-700">Staff</div>}
        {m.attachment && <div className="mb-1 rounded bg-stone-100 px-2 py-1 text-xs">📎 {m.attachment.kind || 'attachment'}</div>}
        <div className="rtl-text whitespace-pre-wrap">{m.text}</div>
        <div className="mt-1 flex flex-wrap items-center justify-end gap-1 text-[10px] text-stone-500">
          {tools.map((t, i) => <span key={i} className="rounded bg-stone-200/70 px-1">🔧 {t}</span>)}
          {m.provider && m.provider !== 'system' && <span>{m.provider}{m.latency_ms ? ` · ${(m.latency_ms / 1000).toFixed(1)}s` : ''}</span>}
          <span>{clock(m.at)}</span>
        </div>
      </div>
    </div>
  )
}
