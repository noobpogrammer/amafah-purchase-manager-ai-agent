import React, { useState, useEffect } from 'react';
import { fetchRFQs, fetchRFQDetail, triggerAIRanking, formatRfqDropdownLabel } from '../api';
import { Sparkles, Trophy, CheckCircle, BarChart3, AlertCircle, Clock } from 'lucide-react';

const formatCurrencyInText = (text) => {
  if (!text || typeof text !== 'string') return text;
  return text.replace(/\$(\d+(?:\.\d+)?)/g, 'AED $1');
};

export default function QuotesReportView({ selectedRfqId, setSelectedRfqId }) {
  const [rfqs, setRfqs] = useState([]);
  const [reportData, setReportData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [rankingLoading, setRankingLoading] = useState(false);

  useEffect(() => {
    async function loadRFQsList() {
      try {
        const data = await fetchRFQs();
        const sorted = (data || []).sort(
          (a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)
        );
        setRfqs(sorted);
        if (!selectedRfqId && sorted.length > 0) {
          setSelectedRfqId(sorted[0].id);
        }
      } catch (err) {
        console.error('Error fetching RFQs:', err);
      } finally {
        setLoading(false);
      }
    }
    loadRFQsList();
  }, []);

  useEffect(() => {
    async function loadReport() {
      if (!selectedRfqId) return;
      setLoading(true);
      try {
        const data = await fetchRFQDetail(selectedRfqId);
        setReportData(data);
      } catch (err) {
        console.error('Error loading report:', err);
      } finally {
        setLoading(false);
      }
    }
    loadReport();
  }, [selectedRfqId]);

  const handleTriggerRanking = async () => {
    if (!selectedRfqId) return;
    setRankingLoading(true);
    try {
      await triggerAIRanking(selectedRfqId);
      const updated = await fetchRFQDetail(selectedRfqId);
      setReportData(updated);
    } catch (err) {
      console.error(err);
      alert('Ranking calculation failed: ' + err.message);
    } finally {
      setRankingLoading(false);
    }
  };

  const currentRfq = reportData?.rfq;
  const quotes = reportData?.quotes || [];
  const ranking = reportData?.ranking;

  const getSupplierName = (supplierId) => {
    if (!supplierId) return null;
    const quoteMatch = quotes.find((q) => q.supplier_id === supplierId);
    if (quoteMatch?.suppliers?.name) return quoteMatch.suppliers.name;
    const supplierMatch = reportData?.suppliers?.find(
      (s) => s.supplier_id === supplierId || s.suppliers?.id === supplierId || s.id === supplierId
    );
    if (supplierMatch?.suppliers?.name) return supplierMatch.suppliers.name;
    if (supplierMatch?.name) return supplierMatch.name;
    return null;
  };

  // Find best offer details from ranking
  const bestQuoteId = ranking?.best_quote_id || ranking?.ranking_json?.best_quote_id || null;
  const winningQuote = bestQuoteId ? quotes.find((q) => q.id === bestQuoteId) : null;

  let bestOfferTitle = 'Best Offer';
  let bestOfferSub = null;

  if (winningQuote) {
    const suppName = winningQuote.suppliers?.name || getSupplierName(winningQuote.supplier_id) || 'Supplier';
    const varText = winningQuote.variant_label ? ` — ${winningQuote.variant_label}` : '';
    bestOfferTitle = `${suppName}${varText}`;
    bestOfferSub = `AED ${winningQuote.price} | Delivery: ${winningQuote.delivery_time || 'Not specified'}${winningQuote.quality_notes ? ` | Notes: ${winningQuote.quality_notes}` : ''}`;
  } else if (ranking?.best_supplier_id) {
    const matched = getSupplierName(ranking.best_supplier_id);
    if (matched) {
      bestOfferTitle = matched;
    }
  }

  return (
    <div className="view-container">
      <div className="view-header">
        <div>
          <h2 className="view-title">Quotes Comparison & AI Recommendation Report</h2>
          <p className="view-description">
            Evaluates price, delivery speed, and quality notes across commercial offers to recommend the optimal choice.
          </p>
        </div>
        {/* RFQ Switcher Dropdown */}
        <div className="rfq-select-dropdown">
          <label className="form-label">Select RFQ:</label>
          <select
            className="input-field select-input"
            value={selectedRfqId || ''}
            onChange={(e) => setSelectedRfqId(e.target.value)}
          >
            {rfqs.map((r) => (
              <option key={r.id} value={r.id}>
                {formatRfqDropdownLabel(r)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {loading ? (
        <div className="loading-state">Loading procurement comparison report...</div>
      ) : !reportData || !currentRfq ? (
        <div className="card empty-card">Please select an RFQ to view comparative report.</div>
      ) : (
        <div className="report-layout">
          {/* RFQ Overview Header */}
          <div className="card report-header-card">
            <div className="flex-between">
              <div>
                <span className="badge badge-category">{currentRfq.category}</span>
                <h3 className="report-title">{currentRfq.product_name}</h3>
                <p className="report-sub">
                  Specs: {currentRfq.specs || 'Standard'} | Quantity: {currentRfq.quantity || 'N/A'} | Deadline: {currentRfq.deadline_hours || 24}h
                </p>
              </div>
              <button
                className="btn btn-primary"
                onClick={handleTriggerRanking}
                disabled={rankingLoading || quotes.length === 0}
              >
                <Sparkles size={18} />
                <span>{rankingLoading ? 'Evaluating Quotes...' : 'Run AI Recommendation'}</span>
              </button>
            </div>
          </div>

          {/* AI Recommendation Hero Banner */}
          {ranking ? (
            <div className="ai-recommendation-hero">
              <div className="hero-badge">
                <Trophy size={20} className="trophy-icon" /> AI Recommended Offer
              </div>
              <h3 className="hero-supplier-name">AI Recommends: {bestOfferTitle}</h3>
              {bestOfferSub && (
                <p style={{ margin: '0.25rem 0 0.5rem 0', fontWeight: 600, color: 'var(--color-primary-light, #38bdf8)' }}>
                  {bestOfferSub}
                </p>
              )}
              <p className="hero-reasoning">{formatCurrencyInText(ranking.reasoning)}</p>
            </div>
          ) : (
            <div className="card alert-banner info-banner">
              <Sparkles size={24} className="alert-icon" />
              <div>
                <strong>AI Ranking Pending</strong>
                <p>Click "Run AI Recommendation" above to evaluate quotes and generate ranking report.</p>
              </div>
            </div>
          )}

          {/* Supplier Comparison Table */}
          <div className="card">
            <div className="card-header flex-between">
              <h4 className="card-title flex-items">
                <BarChart3 size={18} />
                <span>Received Quotes & Variants ({quotes.length})</span>
              </h4>
            </div>

            {quotes.length === 0 ? (
              <div className="empty-state">
                <Clock size={36} />
                <p>No quotes recorded yet for this RFQ. Awaiting supplier replies on WhatsApp.</p>
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Supplier</th>
                    <th>Variant</th>
                    <th>Quoted Price</th>
                    <th>Delivery Time</th>
                    <th>Quality / Warranty</th>
                    <th>Historical Reliability</th>
                    <th>Raw WhatsApp Reply</th>
                  </tr>
                </thead>
                <tbody>
                  {quotes.map((q) => {
                    const isBest = bestQuoteId
                      ? q.id === bestQuoteId
                      : (ranking?.best_supplier_id === q.supplier_id && quotes.filter((x) => x.supplier_id === ranking.best_supplier_id).length === 1);

                    return (
                      <tr key={q.id} className={isBest ? 'row-highlight' : ''}>
                        <td>
                          <strong>{q.suppliers?.name || 'Supplier'}</strong>
                          {isBest && (
                            <span className="badge badge-best" style={{ marginLeft: '0.5rem' }}>
                              <CheckCircle size={12} /> Best Value
                            </span>
                          )}
                        </td>
                        <td>
                          {q.variant_label ? (
                            <span className="badge badge-category">{q.variant_label}</span>
                          ) : (
                            <span style={{ color: 'var(--text-muted)' }}>—</span>
                          )}
                        </td>
                        <td>
                          <span className="price-tag-large">AED {q.price}</span>
                        </td>
                        <td>{q.delivery_time || 'Not specified'}</td>
                        <td>{q.quality_notes || 'Standard'}</td>
                        <td>
                          <span className="badge badge-history">Insufficient history</span>
                        </td>
                        <td className="raw-msg-cell">
                          <span className="raw-msg-text">"{q.raw_message}"</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          {/* AI Ranking Breakdown */}
          {ranking?.ranking_json?.ranking && (
            <div className="card">
              <div className="card-header">
                <h4 className="card-title flex-items">
                  <Trophy size={18} />
                  <span>AI Comparative Breakdown</span>
                </h4>
              </div>
              <div className="ranking-breakdown-list">
                {ranking.ranking_json.ranking.map((item, idx) => {
                  const supplierName = item.supplier_name || getSupplierName(item.supplier_id) || 'Supplier';
                  const varLabel = item.variant_label ? ` (${item.variant_label})` : '';
                  const priceText = item.price !== undefined && item.price !== null ? ` — AED ${item.price}` : '';
                  return (
                    <div key={idx} className="ranking-item">
                      <div className="rank-badge">#{item.rank}</div>
                      <div className="rank-details">
                        <strong>
                          {supplierName}{varLabel}{priceText}
                        </strong>
                        <p>{formatCurrencyInText(item.summary)}</p>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
