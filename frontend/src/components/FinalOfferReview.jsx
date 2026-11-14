import React, { useState } from 'react';
import { decideBuyerFinalOffer } from '../api';

export default function FinalOfferReview({ review, rfqId, target, maxPrice, isAdmin, onUpdated }) {
  const [counter, setCounter] = useState(target ?? '');
  const [showCounter, setShowCounter] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [receipt, setReceipt] = useState('');
  if (!review) return null;
  const offer = review.offer_snapshot;
  const pending = review.status === 'pending' && !receipt;
  const decide = async (decision) => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      await decideBuyerFinalOffer(rfqId, review.id, decision, decision === 'negotiate' && counter !== '' ? Number(counter) : null);
      setReceipt(decision === 'accept' ? 'Acceptance instruction queued. Confirmation is not sent yet.' : 'Negotiation instruction queued. Your current offer has not been accepted.');
      onUpdated?.();
    } catch (err) { setError(err.message || 'Could not save your decision.'); }
    finally { setBusy(false); }
  };
  return <section className="final-offer-review" aria-label="Buyer final offer review">
    <div className="final-offer-heading"><strong>Supplier’s final offer</strong><span className="badge badge-warning">{pending ? 'Your decision needed' : review.status === 'completed' ? 'Offer accepted' : review.status === 'stale' ? 'Offer changed' : 'Decision recorded'}</span></div>
    <p>AED {offer.price} per unit · {offer.quantity == null ? 'Quantity unconfirmed' : `${offer.quantity} units`} · {offer.resolved_delivery_days == null ? 'Delivery unconfirmed' : `${offer.resolved_delivery_days} days`}</p>
    <p className="final-offer-help">{target != null ? `Your target is AED ${target}. ` : ''}Should the agent accept this quote or negotiate again?</p>
    {pending && <>
      <div className="final-offer-actions">
        <button className="btn btn-primary" disabled={!isAdmin || busy || maxPrice == null || Number(offer.price) > Number(maxPrice)} onClick={() => decide('accept')}>{busy ? 'Saving…' : `Accept AED ${offer.price}`}</button>
        <button className="btn btn-secondary" disabled={!isAdmin || busy} onClick={() => setShowCounter(!showCounter)}>Negotiate again</button>
      </div>
      {showCounter && <form className="final-offer-counter" onSubmit={(event) => { event.preventDefault(); decide('negotiate'); }}>
        <label htmlFor={`counter-${review.id}`}>Ask for a unit price (AED)</label>
        <div className="final-offer-counter-controls"><input id={`counter-${review.id}`} type="number" inputMode="decimal" step="0.01" min="0.01" max={Number(offer.price) - 0.01} value={counter} onChange={event => setCounter(event.target.value)} placeholder="Use the agent’s strategy" />
          <button className="btn btn-secondary" disabled={busy || (counter !== '' && (!Number.isFinite(Number(counter)) || Number(counter) <= 0 || Number(counter) >= Number(offer.price)))} type="submit">Send negotiation instruction</button></div>
        <small>Leave empty to let the agent negotiate toward your target. No acceptance is authorized.</small>
      </form>}
      {(maxPrice == null || Number(offer.price) > Number(maxPrice)) && <small>This offer is outside the current price limit. Negotiate again; acceptance is unavailable.</small>}
      {!isAdmin && <small>A buyer administrator must approve this decision.</small>}
    </>}
    {(receipt || review.job_error) && <p role="status">{receipt || review.job_error}</p>}
    {!pending && !receipt && <p role="status">{review.status === 'completed' ? 'Acceptance saved. Check conversation tracking for supplier confirmation.' : review.status === 'stale' ? 'The offer changed. Review the latest quotation.' : `${review.decision === 'accept' ? 'Acceptance' : 'Negotiation'} instruction: ${review.job_state || 'queued'}.`}</p>}
    {error && <p role="alert" className="final-offer-error">{error}</p>}
  </section>;
}
