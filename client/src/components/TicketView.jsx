/* Helpers and the ticket body shared by the payment page and the My bookings page, so a train,
   bus and flight ticket all read the same everywhere. */

export const rupees = (n) => '₹' + Number(n || 0).toLocaleString('en-IN')

export const to12 = (t) => {
  if (!t) return ''
  const [h, m] = t.split(':').map(Number)
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`
}

export const KIND_LABEL = { train: 'Train', bus: 'Bus', flight: 'Flight' }

export const ticketTitle = (b) =>
  b.kind === 'train' ? `${b.train_no} ${b.train_name}`
    : b.kind === 'flight' ? `${b.flight_no} · ${b.airline}`
      : `${b.operator} · ${b.type}`

export const refLabel = (b) => (b.kind === 'bus' ? 'Ticket' : 'PNR')

export const statusTone = (s) =>
  s === 'CONFIRMED' ? 'on' : (s === 'RAC' || s === 'PENDING_PAYMENT') ? 'rac' : 'off'

export const statusText = (s) => (s === 'PENDING_PAYMENT' ? 'AWAITING PAYMENT' : s)

/* The grid of facts about a booking. `paid` switches the last cell between Total and Total paid. */
export function TicketFacts({ b }) {
  const ref = b.pnr || b.ticket
  return (
    <dl className="tick-grid">
      {ref && <div><dt>{refLabel(b)}</dt><dd className="mono">{ref}</dd></div>}
      <div><dt>Date</dt><dd>{b.date}</dd></div>
      <div><dt>Departs</dt><dd>{to12(b.depart)}</dd></div>
      {b.kind === 'train' && <>
        <div><dt>Class</dt><dd>{b.cls_label}</dd></div>
        <div><dt>Quota</dt><dd>{b.quota === 'tatkal' ? 'Tatkal' : 'General'}</dd></div>
      </>}
      {b.kind === 'bus' && <div><dt>Seats</dt><dd>{b.seats.join(', ')}</dd></div>}
      {b.kind === 'flight' && <>
        <div><dt>Cabin</dt><dd>{b.cabin_label}</dd></div>
        <div><dt>Route</dt><dd>{b.from} → {b.to}</dd></div>
        <div><dt>Arrives</dt><dd>{to12(b.arrive)}</dd></div>
      </>}
      <div><dt>{b.payment_status === 'PAID' || !b.payment_status ? 'Total paid' : 'Total'}</dt><dd>{rupees(b.total)}</dd></div>
    </dl>
  )
}

export function PassengerList({ list }) {
  return (
    <ul className="tick-pax">
      {list.map((p, i) => <li key={i}><span>{p.name}</span><span className="muted small">{p.age} · {p.gender}</span></li>)}
    </ul>
  )
}

/* Per-passenger fare lines, matching what the server priced. */
export function FareLines({ b }) {
  const f = b.fare_each || {}
  return (
    <ul className="fare">
      {b.kind === 'train' && <>
        <li><span>Base fare</span><span>{rupees(f.base)}</span></li>
        {f.tatkal > 0 && <li><span>Tatkal charge</span><span>{rupees(f.tatkal)}</span></li>}
        <li><span>Reservation fee</span><span>{rupees(f.reservation)}</span></li>
        {f.gst > 0 && <li><span>GST</span><span>{rupees(f.gst)}</span></li>}
      </>}
      {b.kind === 'flight' && <>
        <li><span>Base fare</span><span>{rupees(f.base)}</span></li>
        <li><span>Airport &amp; user fees</span><span>{rupees(f.fees)}</span></li>
        <li><span>GST</span><span>{rupees(f.gst)}</span></li>
      </>}
      {b.kind === 'bus' && <li><span>Seat fare</span><span>{rupees(f.total)}</span></li>}
      <li className="fare-sum"><span>Per passenger</span><span>{rupees(f.total)}</span></li>
    </ul>
  )
}
