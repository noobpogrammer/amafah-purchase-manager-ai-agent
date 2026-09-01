import React, { useState, useEffect } from 'react';
import { fetchRFQs, fetchRFQDetail, triggerAIRanking } from '../api';
import { Sparkles, Trophy, CheckCircle, BarChart3, AlertCircle, Clock } from 'lucide-react';

export default function QuotesReportView({ selectedRfqId, setSelectedRfqId }) {
  const [rfqs, setRfqs] = useState([]);
  const [reportData, setReportData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [rankingLoading, setRankingLoading] = useState(false);

  useEffect(() => {
    async function loadRFQsList() {
      try {
        const data = await fetchRFQs();
        setRfqs(data);
        if (!selectedRfqId && data.length > 0) {
          setSelectedRfqId(data[0].id);
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

  // Find best supplier name from ranking
  let bestSupplierName = 'Best Supplier';
  if (ranking?.best_supplier_id) {
    const matched = quotes.find((q) => q.supplier_id === ranking.best_supplier_id);
    if (matched && matched.suppliers?.name) {
      bestSupplierName = matched.suppliers.name;
    }
  }

  return (
    <div className="view-container">
      <div className="view-header">
        <div>
          <h2 className="view-title">Quotes Comparison & AI Recommendation Report</h2>
          <p className="view-description">
            Evaluates price, delivery speed, and quality notes using Groq AI to rank supplier offers.
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
                {r.product_name} ({r.category})
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
                <Trophy size={20} className="trophy-icon" /> AI Optimal Choice
              </div>
              <h3 className="hero-supplier-name">AI Recommends: {bestSupplierName}</h3>
              <p className="hero-reasoning">{ranking.reasoning}</p>
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
                <span>Received Quotes ({quotes.length})</span>
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
                    <th>Quoted Price</th>
                    <th>Delivery Time</th>
                    <th>Quality / Warranty</th>
                    <th>Historical Reliability</th>
                    <th>Raw WhatsApp Reply</th>
                  </tr>
                </thead>
                <tbody>
                  {quotes.map((q) => {
                    const isBest = ranking?.best_supplier_id === q.supplier_id;
                    return (
                      <tr key={q.id} className={isBest ? 'row-highlight' : ''}>
                        <td>
                          <strong>{q.suppliers?.name || 'Supplier'}</strong>
                          {isBest && (
                            <span className="badge badge-best">
                              <CheckCircle size={12} /> Best Value
                            </span>
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
                {ranking.ranking_json.ranking.map((item, idx) => (
                  <div key={idx} className="ranking-item">
                    <div className="rank-badge">#{item.rank}</div>
                    <div className="rank-details">
                      <strong>Supplier ID: {item.supplier_id}</strong>
                      <p>{item.summary}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
