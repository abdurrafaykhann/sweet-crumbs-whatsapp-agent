import { useState } from 'react'
import { api } from '../api.js'
import { Badge, Card, Drawer, Empty, ErrorNote, StatusBadge, go, inputCls, rs, useLoad, when } from '../ui.jsx'

const LANG = { en: 'English', rur: 'Roman Urdu', ur: 'Urdu' }

export default function Customers({ tick }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)
  const list = useLoad(() => api.customers(q), [q], tick)
  const rows = list.data || []

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Customers</h1>
      <input placeholder="Search name or phone" value={q} onChange={(e) => setQ(e.target.value)} className={inputCls} />
      <ErrorNote error={list.error} />
      {list.data && rows.length === 0 && <Empty>No customers yet.</Empty>}
      <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-stone-200">
        {rows.map((c) => (
          <button key={c.id} onClick={() => setOpen(c.id)} className="grid w-full grid-cols-[1fr_auto] gap-x-3 border-b border-stone-100 px-4 py-3 text-left last:border-0 hover:bg-stone-50">
            <div>
              <div className="font-medium">{c.name || c.phone}</div>
              <div className="text-xs text-stone-500">{c.phone} · {LANG[c.language] || c.language}</div>
            </div>
            <div className="text-right text-sm">
              <div className="font-semibold">{rs(c.spent)}</div>
              <div className="text-xs text-stone-500">{c.orders} orders · {c.points} pts</div>
            </div>
          </button>
        ))}
      </div>
      <Detail id={open} onClose={() => setOpen(null)} />
    </div>
  )
}

function Detail({ id, onClose }) {
  const d = useLoad(() => (id ? api.customer(id) : Promise.resolve(null)), [id]).data
  return (
    <Drawer open={!!id} onClose={onClose} title={d ? d.name || d.phone : 'Customer'}>
      {d && (
        <div className="space-y-4">
          <Card className="space-y-1 text-sm">
            <div>{d.phone} · {LANG[d.language] || d.language}</div>
            <div>Crumbs Club points: <b>{d.points}</b> · Spent <b>{rs(d.spent)}</b></div>
            {d.birthday_month && <div>Birthday month: {d.birthday_month}</div>}
            <button className="font-medium text-crumb-600 underline" onClick={() => go('inbox', d.phone)}>Open chat</button>
          </Card>
          {d.addresses.length > 0 && (
            <Card className="space-y-1 text-sm">
              <div className="font-medium">Saved addresses</div>
              {d.addresses.map((a, i) => <div key={i}><Badge>{a.label}</Badge> {a.text}</div>)}
            </Card>
          )}
          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-stone-600">Orders</h3>
            {d.orders.length === 0 && <Empty>No orders yet.</Empty>}
            {d.orders.map((o) => (
              <button key={o.code} onClick={() => go('orders', o.code)} className="block w-full rounded-xl bg-white p-3 text-left ring-1 ring-stone-200">
                <div className="flex items-center justify-between"><span className="font-medium">{o.code} <StatusBadge status={o.status} label={o.status_label} /></span><span>{rs(o.total)}</span></div>
                <div className="text-sm text-stone-600">{o.items}</div>
                <div className="text-xs text-stone-500">{when(o.created_at)}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </Drawer>
  )
}
