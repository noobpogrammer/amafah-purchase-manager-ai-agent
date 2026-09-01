import React, { useState, useEffect } from 'react';
import { fetchRFQs, fetchRFQDetail, triggerAIRanking, closeRFQ } from '../api';
import {
  FileText,
  Clock,
  CheckCircle,
  AlertCircle,
  HelpCircle,
  RefreshCw,
  BarChart2,
  MessageSquare,
  ChevronRight,
  Sparkles,
  XCircle,
  Check,
  X
} from 'lucide-react';

export default function RFQDetailView({
  selectedRfqId,
  setSelectedRfqId,
  setActiveTab,
  refreshTrigger
}) {
  const [rfqs, setRfqs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [detailData, setDetailData] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [rankingLoading, setRankingLoading] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [closing, setClosing] = useState(false);
  const [filterTab, setFilterTab] = useState('all'); // 'all', 'active', 'closed'

  // Load RFQ list
  const loadRFQs = async () => {
    try {
      const data = await fetchRFQs();
      setRfqs(data || []);
      if (!selectedRfqId && data && data.length > 0) {
        setSelectedRfqId(data[0].id);
      }
    } catch (err) {
      console.error('Error fetching RFQs:', err);
    } finally {
      setLoading(false);
    }
  };

  // Load selected RFQ detail
  const loadRFQDetail = async (rfqId) => {
    if (!rfqId) return;
    setDetailLoading(true);
    try {
      const data = await fetchRFQDetail(rfqId);
      setDetailData(data);
    } catch (err) {
      console.error('Error fetching RFQ detail:', err);
    } finally {
      setDetailLoading(false);
    }
  };

  useEffect(() => {
    loadRFQs();
  }, [refreshTrigger]);

  useEffect(() => {
    if (selectedRfqId) {
      setConfirmClose(false);
      loadRFQDetail(selectedRfqId);
    }
  }, [selectedRfqId]);

  // Polling for live status updates
  useEffect(() => {
    if (!autoRefresh || !selectedRfqId) return;
    const interval = setInterval(() => {
      loadRFQDetail(selectedRfqId);
    }, 5000);
    return () => clearInterval(interval);
  }, [autoRefresh, selectedRfqId]);

  const handleRankClick = async () => {
    if (!selectedRfqId) return;
    setRankingLoading(true);
    try {
      await triggerAIRanking(selectedRfqId);
      await loadRFQDetail(selectedRfqId);
      if (setActiveTab) setActiveTab('quotes_report');
    } catch (err) {
      console.error(err);
      alert('Ranking error: ' + err.message);
    } finally {
      setRankingLoading(false);
    }
  };

  const handleCloseRFQ = async (status = 'closed') => {
    if (!selectedRfqId) return;
    setClosing(true);
    try {
      await closeRFQ(selectedRfqId, status);
      setConfirmClose(false);
      await loadRFQs();
      await loadRFQDetail(selectedRfqId);
    } catch (err) {
      console.error(err);
      alert('Error closing RFQ: ' + err.message);
    } finally {
      setClosing(false);
    }
  };

  const getSupplierStatusBadge = (status) => {
    switch (status) {
      case 'responded':
        return <span className="badge badge-status responded"><CheckCircle size={12} /> Responded</span>;
      case 'clarifying':
        return <span className="badge badge-status clarifying"><HelpCircle size={12} /> Clarifying</span>;
      case 'no_response':
        return <span className="badge badge-status no_response"><AlertCircle size={12} /> No Response</span>;
      default:
        return <span className="badge badge-status sent"><Clock size={12} /> Sent / Waiting</span>;
    }
  };

  const getRfqStatusBadge = (status) => {
    switch (status) {
      case 'closed':
        return <span className="badge badge-status no_response"><XCircle size={12} /> Closed</span>;
      case 'cancelled':
        return <span className="badge badge-status contradiction"><XCircle size={12} /> Cancelled</span>;
      default:
        return <span className="badge badge-status responded"><CheckCircle size={12} /> Active</span>;
    }
  };

  const filteredRfqs = rfqs.filter((r) => {
    if (filterTab === 'active') return r.status === 'active';
    if (filterTab === 'closed') return r.status === 'closed' || r.status === 'cancelled';
    return true;
  });

  return (
    <div className="view-container">
      <div className="view-header">
        <div>
          <h2 className="view-title">RFQs & Live Tracking</h2>
          <p className="view-description">
            Monitor supplier response progress live in real-time as the AI agent logs quotes and handles questions.
          </p>
        </div>
        <div className="flex-items gap-2">
          <button
            className={`btn btn-sm ${autoRefresh ? 'btn-success' : 'btn-secondary'}`}
            onClick={() => setAutoRefresh(!autoRefresh)}
          >
            <RefreshCw size={14} className={autoRefresh ? 'spin' : ''} />
            <span>{autoRefresh ? 'Live Polling ON (5s)' : 'Live Polling OFF'}</span>
          </button>
        </div>
      </div>

      <div className="split-view-layout">
        {/* Left List of RFQs */}
        <div className="rfq-sidebar card">
          <div className="card-header flex-between">
            <h3 className="card-title">All RFQs</h3>
            <div className="filter-chips flex-items gap-1">
              <button
                className={`chip ${filterTab === 'all' ? 'active' : ''}`}
                onClick={() => setFilterTab('all')}
              >
                All
              </button>
              <button
                className={`chip ${filterTab === 'active' ? 'active' : ''}`}
                onClick={() => setFilterTab('active')}
              >
                Active
              </button>
              <button
                className={`chip ${filterTab === 'closed' ? 'active' : ''}`}
                onClick={() => setFilterTab('closed')}
              >
                Closed
              </button>
            </div>
          </div>

          {loading ? (
            <div className="loading-state">Loading RFQs...</div>
          ) : filteredRfqs.length === 0 ? (
            <div className="empty-state">No {filterTab !== 'all' ? filterTab : ''} RFQs found.</div>
          ) : (
            <div className="rfq-list">
              {filteredRfqs.map((rfq) => {
                const isSelected = rfq.id === selectedRfqId;
                const totalMatched = rfq.rfq_suppliers?.length || 0;
                const quotesCount = rfq.quotes?.length || 0;
                const isClosed = rfq.status === 'closed' || rfq.status === 'cancelled';

                return (
                  <div
                    key={rfq.id}
                    className={`rfq-item-card ${isSelected ? 'selected' : ''} ${isClosed ? 'dimmed' : ''}`}
                    onClick={() => setSelectedRfqId(rfq.id)}
                  >
                    <div className="rfq-item-header flex-between">
                      <strong>{rfq.product_name}</strong>
                      <div className="flex-items gap-1">
                        {getRfqStatusBadge(rfq.status)}
                        <span className="badge badge-category">{rfq.category}</span>
                      </div>
                    </div>
                    <div className="rfq-item-meta">
                      <span>Qty: {rfq.quantity || 'N/A'}</span>
                      <span>Quotes: {quotesCount}/{totalMatched}</span>
                    </div>
                    <div className="rfq-item-footer">
                      <span className="timestamp">
                        {new Date(rfq.created_at).toLocaleDateString()}
                      </span>
                      <ChevronRight size={16} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right Detail & Live Status Tracking */}
        <div className="rfq-main-content">
          {detailLoading && !detailData ? (
            <div className="card loading-card">Loading RFQ tracking data...</div>
          ) : !detailData ? (
            <div className="card empty-card">Select an RFQ to view live supplier status.</div>
          ) : (
            <div className="rfq-detail-container">
              {/* Header Info */}
              <div className="card">
                <div className="rfq-detail-header">
                  <div>
                    <div className="flex-items gap-2 mb-1">
                      {getRfqStatusBadge(detailData.rfq.status)}
                      <span className="badge badge-category">{detailData.rfq.category}</span>
                    </div>
                    <h3 className="rfq-product-title">{detailData.rfq.product_name}</h3>
                    <p className="rfq-specs-text">
                      Specs: <strong>{detailData.rfq.specs || 'Standard'}</strong> | Quantity: <strong>{detailData.rfq.quantity || 'N/A'}</strong> | Deadline: <strong>{detailData.rfq.deadline_hours || 24} hours</strong>
                    </p>
                  </div>

                  <div className="rfq-header-actions flex-items gap-2">
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => setActiveTab('quotes_report')}
                    >
                      <BarChart2 size={16} /> Quotes Report
                    </button>

                    <button
                      className="btn btn-primary btn-sm"
                      onClick={handleRankClick}
                      disabled={rankingLoading || detailData.quotes.length === 0 || detailData.rfq.status !== 'active'}
                    >
                      <Sparkles size={16} />
                      {rankingLoading ? 'Ranking...' : 'Trigger AI Ranking'}
                    </button>

                    {detailData.rfq.status === 'active' && (
                      !confirmClose ? (
                        <button
                          className="btn btn-danger btn-sm"
                          onClick={() => setConfirmClose(true)}
                        >
                          <XCircle size={16} /> Close RFQ
                        </button>
                      ) : (
                        <div className="flex-items gap-1 confirm-close-box">
                          <span className="confirm-text">Close RFQ?</span>
                          <button
                            className="btn btn-danger btn-sm"
                            onClick={() => handleCloseRFQ('closed')}
                            disabled={closing}
                          >
                            <Check size={14} /> {closing ? 'Closing...' : 'Yes, Close'}
                          </button>
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => setConfirmClose(false)}
                            disabled={closing}
                          >
                            <X size={14} /> Cancel
                          </button>
                        </div>
                      )
                    )}
                  </div>
                </div>
              </div>

              {/* Supplier Response Tracking Table */}
              <div className="card">
                <div className="card-header flex-between">
                  <h4 className="card-title">Supplier Progress ({detailData.suppliers.length} Matched)</h4>
                  <span className="live-tag"><span className="pulse-dot"></span> Live Updates</span>
                </div>

                {detailData.suppliers.length === 0 ? (
                  <div className="empty-state">No suppliers matched for this RFQ category.</div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Supplier Name</th>
                        <th>Phone</th>
                        <th>Status</th>
                        <th>Sent At</th>
                        <th>Reminders</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detailData.suppliers.map((item) => {
                        const supp = item.suppliers;
                        return (
                          <tr key={item.id}>
                            <td><strong>{supp?.name || 'Supplier'}</strong></td>
                            <td>{supp?.phone_number}</td>
                            <td>{getSupplierStatusBadge(item.status)}</td>
                            <td>{new Date(item.sent_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</td>
                            <td>{item.reminder_count} reminders</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>

              {/* Received Quotes Summary */}
              <div className="card">
                <div className="card-header">
                  <h4 className="card-title">Recorded Quotes ({detailData.quotes.length})</h4>
                </div>
                {detailData.quotes.length === 0 ? (
                  <div className="empty-state">No quotes received yet. Replies via WhatsApp will automatically appear here.</div>
                ) : (
                  <div className="quotes-grid">
                    {detailData.quotes.map((q) => (
                      <div key={q.id} className="quote-summary-card">
                        <div className="quote-header">
                          <strong>{q.suppliers?.name || 'Supplier'}</strong>
                          <span className="price-tag">AED {q.price}</span>
                        </div>
                        <div className="quote-body">
                          <p><strong>Delivery:</strong> {q.delivery_time || '-'}</p>
                          <p><strong>Notes:</strong> {q.quality_notes || '-'}</p>
                          <p className="raw-msg"><em>"{q.raw_message}"</em></p>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
