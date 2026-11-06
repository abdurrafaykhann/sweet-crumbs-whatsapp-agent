import { useState } from 'react'
import { api } from '../api.js'
import { Badge, Button, Card, ErrorNote, inputCls, rs, useLoad } from '../ui.jsx'

export default function Menu({ tick, toast }) {
  const [rev, setRev] = useState(0)
  const menu = useLoad(() => api.menu(), [rev], tick)
  const [error, setError] = useState(null)
  const [cat, setCat] = useState('all')
  const reload = () => setRev((n) => n + 1)
  const run = async (fn, msg) => { setError(null); try { await fn(); toast(msg, 'The bot sees this on its next message.', 3000); reload() } catch (e) { setError(e) } }

  const items = menu.data || []
  const cats = ['all', ...new Set(items.map((p) => p.category))]
  const shown = items.filter((p) => cat === 'all' || p.category === cat)

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Menu & stock</h1>
        <p className="text-sm text-stone-500">Changes apply to the very next customer message. Leave stock empty for made-to-order items.</p>
      </div>
      <div className="flex gap-1 overflow-x-auto pb-1">
        {cats.map((c) => <button key={c} onClick={() => setCat(c)} className={`whitespace-nowrap rounded-full px-3 py-1.5 text-sm capitalize ${cat === c ? 'bg-crumb-600 text-white' : 'bg-white text-stone-600 ring-1 ring-stone-200'}`}>{c}</button>)}
      </div>
      <ErrorNote error={error || menu.error} />
      <div className="grid gap-3 lg:grid-cols-2">
        {shown.map((p) => {
          const out = p.variants.every((v) => v.stock === 0)
          return (
            <Card key={p.sku} className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="font-semibold">{p.name}</span>
                {out && <Badge tone="red">Sold out</Badge>}
                {p.made_to_order && <Badge tone="purple">made to order</Badge>}
                <div className="ml-auto">
                  {out ? <Button variant="soft" onClick={() => run(() => api.backInStock(p.sku, null), `${p.name} is back on sale`)}>Back in stock</Button>
                    : <Button variant="ghost" onClick={() => run(() => api.soldOut(p.sku), `${p.name} marked sold out`)}>Sold out</Button>}
                </div>
              </div>
              <div className="space-y-2">
                {p.variants.map((v) => <VariantRow key={v.id} v={v} run={run} />)}
              </div>
            </Card>
          )
        })}
      </div>
    </div>
  )
}

function VariantRow({ v, run }) {
  const [price, setPrice] = useState(String(v.price))
  const [stock, setStock] = useState(v.stock === null ? '' : String(v.stock))
  const dirty = price !== String(v.price) || stock !== (v.stock === null ? '' : String(v.stock))
  return (
    <div className="grid grid-cols-[1fr_5.5rem_5rem_auto] items-center gap-2 text-sm">
      <div className="truncate">{v.label || 'Standard'} <span className="text-xs text-stone-500">{rs(v.price)}</span></div>
      <input aria-label="Price" type="number" min="0" value={price} onChange={(e) => setPrice(e.target.value)} className={inputCls} />
      <input aria-label="Stock" type="number" min="0" placeholder="∞" value={stock} onChange={(e) => setStock(e.target.value)} className={inputCls} />
      <Button variant="soft" disabled={!dirty} onClick={() => run(() => api.editVariant(v.id, { price: Number(price), stock: stock === '' ? null : Number(stock) }), 'Saved')}>Save</Button>
    </div>
  )
}
