import { useState } from 'react'
import { api } from '../api.js'
import { Badge, Button, Card, Empty, ErrorNote, go, rs, useLoad, when } from '../ui.jsx'

export default function Escalations({ tick, toast }) {
  const [status, setStatus] = useState('open')
  const [rev, setRev] = useState(0)
  const list = useLoad(() => api.escalations(status), [status, rev], tick)
  const [error, setError] = useState(null)

  const resolve = async (e, handBack) => {
    setError(null)
    try { await api.resolve(e.id, handBack); toast('Resolved', handBack ? 'The bot is answering this customer again.' : '', 3000); setRev((n) => n + 1) } catch (err) { setError(err) }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Needs a person</h1>
        <div className="flex gap-1">
          {['open', 'resolved'].map((s) => (
            <button key={s} onClick={() => setStatus(s)} className={`rounded-full px-3 py-1.5 text-sm ${status === s ? 'bg-crumb-600 text-white' : 'bg-white text-stone-600 ring-1 ring-stone-200'}`}>{s === 'open' ? 'Open' : 'Resolved'}</button>
          ))}
        </div>
      </div>
      <ErrorNote error={error || list.error} />
      {list.data && list.data.length === 0 && <Empty>{status === 'open' ? 'Nothing is waiting for you. The bot is handling everything.' : 'No resolved cases yet.'}</Empty>}
      <div className="grid gap-3 lg:grid-cols-2">
        {(list.data || []).map((e) => (
          <Card key={e.id} className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={e.priority === 'high' ? 'red' : 'amber'}>{e.priority}</Badge>
              <span className="font-semibold">{e.reason}</span>
              <span className="ml-auto text-xs text-stone-500">{when(e.created_at)}</span>
            </div>
            <div className="text-sm"><b>{e.customer || 'New customer'}</b> <span className="text-stone-500">{e.phone}</span>{e.order_code && <> · <button className="underline" onClick={() => go('orders', e.order_code)}>{e.order_code}</button></>}</div>
            <p className="text-sm text-stone-700">{e.summary}</p>
            {e.collected && (
              <div className="rounded-lg bg-stone-50 p-2 text-sm">
                <div className="font-medium">Already collected</div>
                <ul className="list-inside list-disc text-stone-700">
                  {e.collected.items?.map((i, k) => <li key={k}>{i}</li>)}
                  {e.collected.address && <li>Address: {e.collected.address}</li>}
                  {e.collected.when && <li>When: {e.collected.when}</li>}
                  {e.collected.total ? <li>Total {rs(e.collected.total)}</li> : null}
                  {e.collected.still_missing?.length > 0 && <li className="text-amber-700">Still missing: {e.collected.still_missing.join(', ')}</li>}
                </ul>
              </div>
            )}
            <div className="space-y-1 rounded-lg bg-[#efeae2] p-2 text-sm">
              {e.last_messages.map((m, i) => <div key={i} className="rtl-text"><span className="text-xs font-semibold text-stone-500">{m.role === 'customer' ? 'Customer' : m.role === 'human' ? 'Staff' : 'Bot'}: </span>{m.text}</div>)}
            </div>
            {status === 'open' && (
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => go('inbox', e.phone)}>Open chat</Button>
                <Button variant="ghost" onClick={() => resolve(e, true)}>Resolve + hand back to bot</Button>
                <Button variant="ghost" onClick={() => resolve(e, false)}>Resolve</Button>
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  )
}
