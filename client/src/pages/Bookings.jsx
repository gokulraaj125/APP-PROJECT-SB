import { useEffect, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { api, useAuth } from '../auth.jsx'
import { KIND_LABEL, PassengerList, TicketFacts, rupees, statusText, statusTone, ticketTitle } from '../components/TicketView.jsx'

export default function Bookings() {
  const { user, ready } = useAuth()
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!user) return
    api('/api/bookings').then(setRows).catch((e) => setErr(e.message))
  }, [user])

  async function cancel(id) {
    try {
      const updated = await api(`/api/bookings/${id}/cancel`, { method: 'POST' })
      setRows((r) => r.map((b) => (b.id === id ? updated : b)))
    } catch (e) {
      setErr(e.message)
    }
  }

  if (!ready) return <main className="section dark page"><p className="muted-l">Loading…</p></main>
  if (!user) return <Navigate to="/login" replace state={{ next: '/bookings' }} />

  return (
    <main className="section dark page">
      <h1 className="big">Your tickets</h1>
      <p className="muted-l">Signed in as {user.email}</p>
      {err && <p className="notice">{err}</p>}
      {rows && rows.length === 0 && (
        <p className="muted-l">Nothing booked yet. Open a destination and look for <strong>Getting there</strong>, or <Link to="/tickets" className="card-cta">book tickets</Link>. <Link to="/" className="card-cta">Browse destinations →</Link></p>
      )}
      <div className="ticket-list">
        {(rows || []).map((b, i) => (
          <motion.article key={b.id} className={'panel tick' + (b.status === 'CANCELLED' ? ' void' : '')}
            initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}>
            <header>
              <span className={'badge ' + statusTone(b.status)}>{statusText(b.status)}</span>
              <span className="chip">{KIND_LABEL[b.kind]}</span>
            </header>
            <h3>{ticketTitle(b)}</h3>
            <p className="muted-l">{b.from_name} → {b.to_name}</p>
            <TicketFacts b={b} />
            <PassengerList list={b.passengers} />
            {b.payment && (
              <p className="muted small pay-meta">Paid with {b.payment.detail} · <span className="mono">{b.payment.txn}</span></p>
            )}
            {b.refund && (
              <p className="muted small pay-meta">Refund of {rupees(b.refund.amount)} initiated · <span className="mono">{b.refund.ref}</span>. {b.refund.note}</p>
            )}
            {b.status === 'PENDING_PAYMENT' && (
              <div className="tick-actions">
                <Link to={`/pay/${b.id}`} className="btn btn-light small-btn">Complete payment</Link>
                <button className="pill" onClick={() => cancel(b.id)}>Cancel</button>
              </div>
            )}
            {(b.status === 'CONFIRMED' || b.status === 'RAC') && (
              <button className="pill" onClick={() => cancel(b.id)}>Cancel booking</button>
            )}
            {b.status === 'EXPIRED' && <p className="muted small">The 10-minute payment window ended, so these seats were released.</p>}
          </motion.article>
        ))}
      </div>
      <p className="muted small demo-note">Simulated tickets and payments. Not valid for travel and not connected to IRCTC, any airline or any operator.</p>
    </main>
  )
}
