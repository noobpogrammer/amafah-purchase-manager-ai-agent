import React, { useState, useEffect } from 'react';
import { fetchRFQs, fetchRFQDetail, triggerAIRanking, closeRFQ, fetchRFQActivity } from '../api';
import { ensureProfile } from '../lib/ensureProfile';
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
  X,
  Activity,
  Shield,
  ChevronDown,
  ChevronUp,
  Send,
  Sliders,
  UserCheck
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

  // Admin Activity / Audit state
  const [isAdmin, setIsAdmin] = useState(false);
  const [activeSection, setActiveSection] = useState('overview'); // 'overview', 'activity'
  const [activityData, setActivityData] = useState([]);
  const [activityLoading, setActivityLoading] = useState(false);
  const [activityError, setActivityError] = useState(null);
  const [expandedEvents, setExpandedEvents] = useState({});

  useEffect(() => {
    (async () => {
      try {
        const p = await ensureProfile();
        setIsAdmin(p?.role === 'admin');
      } catch (e) {
        setIsAdmin(false);
      }
    })();
  }, []);

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

  // Load Activity timeline
  const loadActivity = async (rfqId) => {
    if (!rfqId) return;
    setActivityLoading(true);
    setActivityError(null);
    try {
      const res = await fetchRFQActivity(rfqId);
      setActivityData(res?.activity || []);
    } catch (err) {
      console.error('Error fetching RFQ activity:', err);
      setActivityError(err.message || 'Failed to load activity');
    } finally {
      setActivityLoading(false);
    }
  };

  useEffect(() => {
    loadRFQs();
  }, [refreshTrigger]);

  useEffect(() => {
    if (selectedRfqId) {
      setConfirmClose(false);
      loadRFQDetail(selectedRfqId);
      if (activeSection === 'activity' && isAdmin) {
        loadActivity(selectedRfqId);
      }
    }
  }, [selectedRfqId, activeSection, isAdmin]);

  // Polling for live status updates
  useEffect(() => {
    if (!autoRefresh || !selectedRfqId) return;
    const interval = setInterval(() => {
      loadRFQDetail(selectedRfqId);
      if (activeSection === 'activity' && isAdmin) {
        loadActivity(selectedRfqId);
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [autoRefresh, selectedRfqId, activeSection, isAdmin]);

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

  const toggleExpand = (id) => {
    setExpandedEvents((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const getSupplierStatusBadge = (status) => {
    switch (status) {
      case 'responded':
        return <span className="badge badge-status responded"><CheckCircle size={12} /> Responded</span>;
      case 'clarifying':
        return <span className="badge badge-status clarifying"><HelpCircle size={12} /> Clarifying</span>;
      case 'no_response':
        return <span className="badge badge-status no-response"><AlertCircle size={12} /> No Response</span>;
      case 'sent':
      default:
        return <span className="badge badge-status sent"><Clock size={12} /> Sent / Pending</span>;
    }
  };

  const getRfqStatusBadge = (status) => {
    switch (status) {
      case 'active':
        return <span className="badge badge-status active"><Clock size={12} /> Active</span>;
      case 'closed':
        return <span className="badge badge-status closed"><CheckCircle size={12} /> Closed</span>;
      case 'cancelled':
        return <span className="badge badge-status cancelled"><XCircle size={12} /> Cancelled</span>;
      default:
        return <span className="badge badge-status">{status}</span>;
    }
  };

  const filteredRFQs = rfqs.filter((r) => {
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
            <div className="segmented-control">
              <button
                className={`segmented-btn ${filterTab === 'all' ? 'active' : ''}`}
                onClick={() => setFilterTab('all')}
              >
                All
              </button>
              <button
                className={`segmented-btn ${filterTab === 'active' ? 'active' : ''}`}
                onClick={() => setFilterTab('active')}
              >
                Active
              </button>
              <button
                className={`segmented-btn ${filterTab === 'closed' ? 'active' : ''}`}
                onClick={() => setFilterTab('closed')}
              >
                Closed
              </button>
            </div>
          </div>

          {loading ? (
            <div className="loading-state">Loading RFQs...</div>
          ) : filteredRFQs.length === 0 ? (
            <div className="empty-state">No {filterTab !== 'all' ? filterTab : ''} RFQs found.</div>
          ) : (
            <div className="rfq-list">
              {filteredRFQs.map((rfq) => {
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
                      {detailData.rfq.last_quote && (
                        <> | Last Quote: <strong>AED {detailData.rfq.last_quote}</strong> (Target: <strong>AED {detailData.rfq.last_quote}</strong>, Final Tolerance: Up to <strong>AED {Number(detailData.rfq.last_quote) + 2}</strong>)</>
                      )}
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

              {/* View Section Tabs */}
              <div className="flex-items gap-2 mb-3">
                <button
                  className={`btn btn-sm ${activeSection === 'overview' ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={() => setActiveSection('overview')}
                >
                  <FileText size={14} /> Overview & Quotes
                </button>
                {isAdmin && (
                  <button
                    className={`btn btn-sm ${activeSection === 'activity' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => {
                      setActiveSection('activity');
                      loadActivity(selectedRfqId);
                    }}
                  >
                    <Activity size={14} /> Activity & Audit Log
                  </button>
                )}
              </div>

              {activeSection === 'overview' && (
                <>
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
                        {detailData.quotes.map((q) => {
                          const bestQuoteId = detailData.ranking?.best_quote_id || detailData.ranking?.ranking_json?.best_quote_id;
                          const isBest = bestQuoteId
                            ? bestQuoteId === q.id
                            : (detailData.ranking?.best_supplier_id === q.supplier_id && detailData.quotes.filter((x) => x.supplier_id === detailData.ranking.best_supplier_id).length === 1);

                          return (
                            <div key={q.id} className={`quote-summary-card ${isBest ? 'row-highlight' : ''}`}>
                              <div className="quote-header">
                                <div>
                                  <strong>{q.suppliers?.name || 'Supplier'}</strong>
                                  {q.variant_label && (
                                    <span className="badge badge-category" style={{ marginLeft: '0.4rem' }}>
                                      {q.variant_label}
                                    </span>
                                  )}
                                  {isBest && (
                                    <span className="badge badge-best" style={{ marginLeft: '0.4rem' }}>
                                      <CheckCircle size={12} /> Best Offer
                                    </span>
                                  )}
                                </div>
                                <span className="price-tag">AED {q.price}</span>
                              </div>
                              <div className="quote-body">
                                <p><strong>Delivery:</strong> {q.delivery_time || '-'}</p>
                                <p><strong>Notes:</strong> {q.quality_notes || '-'}</p>
                                <p className="raw-msg"><em>"{q.raw_message}"</em></p>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </>
              )}

              {activeSection === 'activity' && isAdmin && (
                <div className="card">
                  <div className="card-header flex-between">
                    <div>
                      <h4 className="card-title">RFQ Activity & Decision Audit Timeline</h4>
                      <p className="text-muted text-sm" style={{ marginTop: '0.2rem' }}>
                        Chronological provenance of inbound messages, agent proposals, validator checks, and outbound actions.
                      </p>
                    </div>
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => loadActivity(selectedRfqId)}
                      disabled={activityLoading}
                    >
                      <RefreshCw size={14} className={activityLoading ? 'spin' : ''} /> Refresh
                    </button>
                  </div>

                  {activityLoading && activityData.length === 0 ? (
                    <div className="loading-state">Loading timeline events...</div>
                  ) : activityError ? (
                    <div className="msg error">{activityError}</div>
                  ) : activityData.length === 0 ? (
                    <div className="empty-state">No activity events recorded yet for this RFQ.</div>
                  ) : (
                    <div className="activity-timeline" style={{ padding: '1rem 0' }}>
                      {activityData.map((ev, idx) => {
                        const isExpanded = !!expandedEvents[ev.id];
                        const dateStr = ev.timestamp ? new Date(ev.timestamp).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '-';

                        const isRejected = ev.event_type === 'decision_rejected' || ev.status === 'rejected';
                        const isDeliveryIssue = ev.status === 'failed' || ev.status === 'unknown';

                        return (
                          <div
                            key={ev.id || idx}
                            className={`timeline-item ${isRejected ? 'timeline-rejected' : ''} ${isDeliveryIssue ? 'timeline-delivery-issue' : ''}`}
                            style={{
                              borderLeft: isRejected ? '3px solid #ef4444' : isDeliveryIssue ? '3px solid #f59e0b' : '3px solid #3b82f6',
                              paddingLeft: '1.25rem',
                              marginBottom: '1.5rem',
                              position: 'relative'
                            }}
                          >
                            <div className="flex-between flex-wrap gap-1" style={{ marginBottom: '0.25rem' }}>
                              <div className="flex-items gap-2">
                                <span className="timeline-time text-xs text-muted font-mono">{dateStr}</span>
                                <span className="badge badge-sm" style={{ textTransform: 'capitalize' }}>
                                  {ev.origin || 'system'}
                                </span>
                                {ev.status && (
                                  <span className={`badge badge-sm ${isRejected ? 'badge-danger' : isDeliveryIssue ? 'badge-warning' : 'badge-status'}`}>
                                    {ev.status}
                                  </span>
                                )}
                              </div>
                              {ev.supplier_name && (
                                <span className="badge badge-category badge-sm">{ev.supplier_name}</span>
                              )}
                            </div>

                            <div style={{ fontWeight: 600, fontSize: '0.95rem', color: isRejected ? '#dc2626' : 'inherit' }}>
                              {ev.title}
                            </div>

                            <div style={{ fontSize: '0.875rem', color: '#4b5563', marginTop: '0.2rem', lineHeight: '1.4' }}>
                              {ev.summary}
                            </div>

                            {/* Technical Details Toggle */}
                            {ev.details && Object.keys(ev.details).length > 0 && (
                              <div style={{ marginTop: '0.5rem' }}>
                                <button
                                  className="btn btn-ghost btn-xs text-muted"
                                  onClick={() => toggleExpand(ev.id)}
                                  style={{ padding: '0.2rem 0.4rem', fontSize: '0.75rem', cursor: 'pointer' }}
                                >
                                  {isExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                                  {isExpanded ? ' Hide Technical Details' : ' View Technical Details'}
                                </button>

                                {isExpanded && (
                                  <pre
                                    style={{
                                      background: '#f8fafc',
                                      border: '1px solid #e2e8f0',
                                      borderRadius: '6px',
                                      padding: '0.6rem',
                                      fontSize: '0.75rem',
                                      marginTop: '0.4rem',
                                      overflowX: 'auto',
                                      color: '#1e293b'
                                    }}
                                  >
                                    {JSON.stringify(ev.details, null, 2)}
                                  </pre>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
