// Thin client for /api/admin. The JWT lives in localStorage; a 401 anywhere logs the owner out.
const KEY = 'sc_token'
let onUnauthorized = () => {}

export const token = () => {
  try { return localStorage.getItem(KEY) } catch { return null }
}
export const setToken = (t) => {
  try { t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY) } catch { /* private mode */ }
}
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn }

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === 'string' ? detail : detail?.error || `HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

async function call(method, path, body) {
  const res = await fetch(`/api/admin${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token() ? { Authorization: `Bearer ${token()}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (res.status === 401 && path !== '/login') onUnauthorized()
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, data.detail ?? data)
  return data
}

const qs = (params) => {
  const p = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') p.set(k, v) })
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const api = {
  login: (password) => call('POST', '/login', { password }),
  orders: (params) => call('GET', `/orders${qs(params)}`),
  order: (code) => call('GET', `/orders/${code}`),
  setStatus: (code, status, extra = {}) => call('POST', `/orders/${code}/status`, { status, ...extra }),
  payment: (code, action, extra = {}) => call('POST', `/orders/${code}/payment`, { action, ...extra }),
  editOrder: (code, changes) => call('PATCH', `/orders/${code}`, changes),
  cancelOrder: (code, reason) => call('POST', `/orders/${code}/cancel`, { reason }),
  conversations: (params) => call('GET', `/conversations${qs(params)}`),
  conversation: (phone) => call('GET', `/conversations/${encodeURIComponent(phone)}`),
  takeOver: (phone) => call('POST', `/conversations/${encodeURIComponent(phone)}/take-over`),
  reply: (phone, text) => call('POST', `/conversations/${encodeURIComponent(phone)}/reply`, { text }),
  handBack: (phone) => call('POST', `/conversations/${encodeURIComponent(phone)}/hand-back`),
  escalations: (status = 'open') => call('GET', `/escalations${qs({ status })}`),
  resolve: (id, hand_back = false) => call('POST', `/escalations/${id}/resolve`, { hand_back }),
  menu: () => call('GET', '/menu'),
  editVariant: (id, changes) => call('PATCH', `/variants/${id}`, changes),
  soldOut: (sku) => call('POST', `/products/${sku}/sold-out`),
  backInStock: (sku, stock) => call('POST', `/products/${sku}/back-in-stock`, { stock }),
  customers: (q) => call('GET', `/customers${qs({ q })}`),
  customer: (id) => call('GET', `/customers/${id}`),
  analytics: (days = 7) => call('GET', `/analytics${qs({ days })}`),
  updates: (w) => call('GET', `/updates${qs({ after_order: w.order, after_escalation: w.escalation, after_message: w.message })}`),
}
