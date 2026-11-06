import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Badge, Button, Card, Drawer, Empty, ErrorNote, Field, StatusBadge, go, inputCls, rs, useLoad, when } from '../ui.jsx'

const STATUSES = [
  ['', 'All'], ['awaiting_advance', 'Waiting for advance'], ['confirmed', 'Confirmed'], ['scheduled', 'Scheduled'], ['preparing', 'Preparing'],
  ['ready_for_pickup', 'Ready for pickup'], ['out_for_delivery', 'Out for delivery'], ['delivered', 'Delivered'], ['cancelled', 'Cancelled'],
]
const ACTION_LABEL = {
  preparing: 'Start preparing', ready_for_pickup: 'Ready for pickup', out_for_delivery: 'Out for delivery', delivered: 'Mark delivered',
  confirmed: 'Confirm', cancelled: 'Cancel order',
}

export default function Orders({ tick, arg, toast }) {
  const [filters, setFilters] = useState({ status: '', date: '', fulfilment: '', q: '' })
  const [rev, setRev] = useState(0)
  const list = useLoad(() => api.orders({ ...filters, limit: 100 }), [filters.status, filters.date, filters.fulfilment, filters.q, rev], tick)
  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <h1 className="text-xl font-bold">Orders</h1>
        <div className="text-sm text-stone-500">{list.data ? `${list.data.total} order${list.data.total === 1 ? '' : 's'}` : ''}</div>
      </div>

      <div className="flex gap-1 overflow-x-auto pb-1">
        {STATUSES.map(([id, label]) => (
          <button key={id} onClick={() => setFilters((f) => ({ ...f, status: id }))} className={`whitespace-nowrap rounded-full px-3 py-1.5 text-sm ${filters.status === id ? 'bg-crumb-600 text-white' : 'bg-white text-stone-600 ring-1 ring-stone-200'}`}>{label}</button>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <input placeholder="Search code, name, phone" value={filters.q} onChange={set('q')} className={`${inputCls} col-span-2`} />
        <input type="date" value={filters.date} onChange={set('date')} className={inputCls} aria-label="Date" />
        <select value={filters.fulfilment} onChange={set('fulfilment')} className={inputCls} aria-label="Delivery or pickup">
          <option value="">Delivery + pickup</option><option value="delivery">Delivery</option><option value="pickup">Pickup</option>
        </select>
      </div>

      <ErrorNote error={list.error} />
      {list.data && list.data.items.length === 0 && <Empty>No orders match.</Empty>}
      <div className="space-y-2">
        {(list.data?.items || []).map((o) => (
          <button key={o.code} onClick={() => go('orders', o.code)} className="block w-full rounded-2xl bg-white p-3 text-left shadow-sm ring-1 ring-stone-200 hover:ring-crumb-500">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="font-semibold">{o.code}</span>
                <StatusBadge status={o.status} label={o.status_label} />
                {o.source === 'staff' && <Badge tone="purple">staff</Badge>}
                {o.status === 'awaiting_advance' && <Badge tone="amber">check payment</Badge>}
              </div>
              <div className="font-semibold">{rs(o.total)}</div>
            </div>
            <div className="mt-1 text-sm text-stone-700">{o.items}</div>
            <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-stone-500">
              <span>{o.customer.name || o.customer.phone}</span>
              <span>{o.fulfilment === 'pickup' ? `Pickup · ${o.branch || ''}` : `Delivery · ${o.address || ''}`}</span>
              <span>{o.asap ? 'ASAP' : when(o.slot_start)}</span>
            </div>
          </button>
        ))}
      </div>

      <OrderDrawer code={arg} toast={toast} tick={tick} onChanged={() => setRev((n) => n + 1)} />
    </div>
  )
}

function OrderDrawer({ code, toast, tick, onChanged }) {
  const [rev, setRev] = useState(0)
  const detail = useLoad(() => (code ? api.order(code) : Promise.resolve(null)), [code, rev], tick)
  const [rider, setRider] = useState('')
  const [eta, setEta] = useState('')
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [edit, setEdit] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const o = detail.data

  useEffect(() => { setRider(''); setEta(''); setAmount(''); setNote(''); setEdit(null); setError(null) }, [code])

  const run = async (fn, okText) => {
    setBusy(true); setError(null)
    try {
      const r = await fn()
      toast(okText, r.customer_notified ? 'The customer was told on WhatsApp.' : '', 4000)
      setRev((n) => n + 1)
      onChanged()
    } catch (e) {
      setError(new Error(e.detail?.error === 'rider_name_required' ? 'Add the rider name first.' : e.detail?.error ? e.detail.error.replaceAll('_', ' ') : e.message))
    } finally { setBusy(false) }
  }

  return (
    <Drawer open={!!code} onClose={() => go('orders')} title={code ? `Order ${code}` : ''}>
      {!o && <div className="text-sm text-stone-500">{detail.error ? <ErrorNote error={detail.error} /> : 'Loading…'}</div>}
      {o && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={o.status} label={o.status_label} />
            <Badge>{o.fulfilment === 'pickup' ? 'Pickup' : 'Delivery'}</Badge>
            {o.is_gift && <Badge tone="purple">Gift</Badge>}
            <button className="ml-auto text-sm font-medium text-crumb-600 underline" onClick={() => go('inbox', o.customer.phone)}>Open chat</button>
          </div>

          <Card>
            <div className="font-medium">{o.customer.name || 'Customer'} <span className="font-normal text-stone-500">{o.customer.phone}</span></div>
            <div className="mt-2 space-y-1 text-sm">
              {o.lines.map((l, i) => (
                <div key={i} className="flex justify-between gap-2"><span>{l.qty} × {l.name}{l.options?.message ? ` — “${l.options.message}”` : ''}{l.options?.free ? ' (free)' : ''}</span><span>{rs(l.line_total)}</span></div>
              ))}
              <hr className="my-2 border-stone-200" />
              <div className="flex justify-between text-stone-600"><span>Subtotal</span><span>{rs(o.subtotal)}</span></div>
              {o.delivery_fee > 0 && <div className="flex justify-between text-stone-600"><span>Delivery</span><span>{rs(o.delivery_fee)}</span></div>}
              {o.discount > 0 && <div className="flex justify-between text-emerald-700"><span>Discount</span><span>− {rs(o.discount)}</span></div>}
              <div className="flex justify-between font-semibold"><span>Total</span><span>{rs(o.total)}</span></div>
            </div>
          </Card>

          <Card className="space-y-1 text-sm">
            <div><span className="text-stone-500">When:</span> {o.asap ? 'As soon as possible' : `${when(o.slot_start)} – ${when(o.slot_end).split(', ').pop()}`}</div>
            {o.fulfilment === 'delivery' ? <div><span className="text-stone-500">Address:</span> {o.address}{o.zone ? ` (zone ${o.zone})` : ''}</div> : <div><span className="text-stone-500">Branch:</span> {o.branch}</div>}
            {o.receiver_name && <div><span className="text-stone-500">Receiver:</span> {o.receiver_name} {o.receiver_phone}</div>}
            {o.cake_message && <div><span className="text-stone-500">Cake message:</span> “{o.cake_message}”</div>}
            {o.notes && <div><span className="text-stone-500">Notes:</span> {o.notes}</div>}
            {o.rider && <div><span className="text-stone-500">Rider:</span> {o.rider}{o.eta ? ` · ETA ${when(o.eta)}` : ''}</div>}
            <div><span className="text-stone-500">Payment:</span> {o.payment_method} · {o.payment_status}{o.advance_due ? ` · advance ${rs(o.advance_due)}${o.advance_paid ? ` (paid ${rs(o.advance_paid)})` : ' (not verified)'}` : ''}</div>
          </Card>

          {o.status === 'awaiting_advance' && (
            <Card className="space-y-3 border-amber-300 bg-amber-50">
              <div className="font-medium text-amber-900">Advance of {rs(o.advance_due)} to verify</div>
              <p className="text-sm text-amber-900">Only a person confirms money. Check your bank or wallet before confirming.</p>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Amount received"><input type="number" placeholder={String(o.advance_due)} value={amount} onChange={(e) => setAmount(e.target.value)} className={inputCls} /></Field>
                <Field label="Note"><input value={note} onChange={(e) => setNote(e.target.value)} className={inputCls} /></Field>
              </div>
              <div className="flex gap-2">
                <Button disabled={busy} onClick={() => run(() => api.payment(o.code, 'confirm', { amount: amount ? Number(amount) : undefined, note }), 'Advance confirmed')}>Payment received</Button>
                <Button variant="danger" disabled={busy} onClick={() => run(() => api.payment(o.code, 'reject', { note }), 'Customer asked for a clearer screenshot')}>Could not verify</Button>
              </div>
            </Card>
          )}

          {o.next_statuses.filter((s) => s !== 'cancelled').length > 0 && (
            <Card className="space-y-3">
              <div className="font-medium">Move this order forward</div>
              {o.next_statuses.includes('out_for_delivery') && (
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Rider name"><input value={rider} onChange={(e) => setRider(e.target.value)} className={inputCls} placeholder="e.g. Bilal" /></Field>
                  <Field label="ETA (HH:MM, optional)"><input value={eta} onChange={(e) => setEta(e.target.value)} className={inputCls} placeholder="18:30" /></Field>
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                {o.next_statuses.filter((s) => s !== 'cancelled').map((s) => (
                  <Button key={s} disabled={busy} onClick={() => run(() => api.setStatus(o.code, s, s === 'out_for_delivery' ? { rider_name: rider, eta: eta || undefined } : {}), ACTION_LABEL[s] || s)}>{ACTION_LABEL[s] || s}</Button>
                ))}
              </div>
              <p className="text-xs text-stone-500">The customer gets a WhatsApp message in their own language.</p>
            </Card>
          )}

          <ErrorNote error={error} />

          {!['delivered', 'cancelled'].includes(o.status) && (
            <Card className="space-y-3">
              {edit ? (
                <>
                  <Field label="Address"><input className={inputCls} value={edit.address} onChange={(e) => setEdit({ ...edit, address: e.target.value })} /></Field>
                  <Field label="Notes"><input className={inputCls} value={edit.notes} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></Field>
                  <Field label="Cake message"><input className={inputCls} value={edit.cake_message} onChange={(e) => setEdit({ ...edit, cake_message: e.target.value })} /></Field>
                  <div className="flex gap-2">
                    <Button disabled={busy} onClick={() => run(async () => { const r = await api.editOrder(o.code, edit); setEdit(null); return r }, 'Order updated')}>Save</Button>
                    <Button variant="ghost" onClick={() => setEdit(null)}>Cancel</Button>
                  </div>
                </>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <Button variant="ghost" onClick={() => setEdit({ address: o.address || '', notes: o.notes || '', cake_message: o.cake_message || '' })}>Edit details</Button>
                  <Button variant="danger" disabled={busy} onClick={() => window.confirm(`Cancel ${o.code}? The customer will be told.`) && run(() => api.cancelOrder(o.code, 'cancelled by staff'), 'Order cancelled')}>Cancel order</Button>
                </div>
              )}
            </Card>
          )}

          <div>
            <h3 className="mb-2 text-sm font-semibold text-stone-600">Timeline</h3>
            <ol className="space-y-2 border-l-2 border-stone-200 pl-4">
              {o.events.map((e, i) => (
                <li key={i} className="relative text-sm">
                  <span className="absolute -left-[1.4rem] top-1.5 h-2 w-2 rounded-full bg-crumb-500" />
                  <div>{e.text}</div><div className="text-xs text-stone-500">{when(e.at)}</div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </Drawer>
  )
}
