import React, { useState, useEffect } from 'react';
import { fetchFlags, resolveFlag, respondToFlag, authorizeNegotiationTradeoff, resolveFinalQuoteDecision } from '../api';
import { AlertTriangle, CheckCircle2, Phone, Clock, FileText, Check, Send, Sliders, ShieldCheck, XCircle } from 'lucide-react';

export default function AgentAttentionView({ refreshFlagsCount }) {
  const [flags, setFlags] = useState([]);
  const [loading, setLoading] = useState(true);
  const [resolvingId, setResolvingId] = useState(null);

  // Response form states
  const [responseTexts, setResponseTexts] = useState({});
  const [sendToSupplierState, setSendToSupplierState] = useState({});
  const [showCustomConfig, setShowCustomConfig] = useState({});
  const [customFormState, setCustomFormState] = useState({});

  const loadFlags = async () => {
    setLoading(true);
    try {
      const data = await fetchFlags();
      setFlags(data || []);
      if (refreshFlagsCount) refreshFlagsCount();
    } catch (err) {
      console.error('Error fetching flags:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFlags();
  }, []);

  const handleResolve = async (flagId) => {
    setResolvingId(flagId);
    try {
      await resolveFlag(flagId);
      await loadFlags();
    } catch (err) {
      console.error(err);
      alert('Error resolving flag: ' + err.message);
    } finally {
      setResolvingId(null);
    }
  };

  const handleRespond = async (flagId) => {
    const text = (responseTexts[flagId] || '').trim();
    if (!text) return;

    const sendToSupplier = sendToSupplierState[flagId] !== false;
    setResolvingId(flagId);
    try {
      await respondToFlag(flagId, text, sendToSupplier);
      setResponseTexts((prev) => ({ ...prev, [flagId]: '' }));
      await loadFlags();
    } catch (err) {
      console.error(err);
      alert('Error sending human response: ' + err.message);
    } finally {
      setResolvingId(null);
    }
  };

  const handleFinalQuoteDecision = async (flag, decision) => {
    setResolvingId(flag.id);
    try {
      await resolveFinalQuoteDecision(flag.id, decision);
      await loadFlags();
    } catch (err) {
      console.error(err);
      alert('Error resolving final quote decision: ' + err.message);
    } finally {
      setResolvingId(null);
    }
  };

  const handleTradeoffDecision = async (flag, decision, customConstraints = null) => {
    setResolvingId(flag.id);
    const meta = flag.metadata || {};
    const dimension = meta.dimension || 'delivery';

    let constraints = {};
    if (decision === 'approve') {
      if (customConstraints) {
        constraints = customConstraints;
      } else if (dimension === 'delivery') {
        constraints = { max_days: meta.supplier_proposed_value || 5 };
      } else if (dimension === 'quantity') {
        constraints = {
          min: meta.current_value || 1,
          max: meta.supplier_proposed_value || (meta.current_value ? meta.current_value * 2 : 50)
        };
      } else if (dimension === 'specification') {
        constraints = { allowed_alternatives: String(meta.supplier_proposed_value || '') };
      }
    }

    try {
      await authorizeNegotiationTradeoff(flag.id, {
        dimension,
        decision,
        constraints,
        resume_negotiation: true
      });
      await loadFlags();
    } catch (err) {
      console.error(err);
      alert('Error saving authorization: ' + err.message);
    } finally {
      setResolvingId(null);
    }
  };

  const getCategoryBadge = (cat, meta) => {
    if (cat === 'supplier_final_quote_decision' || meta?.type === 'supplier_final_quote_decision') {
      return (
        <span
          className="badge badge-flag business"
          style={{ background: '#0f766e', color: '#ffffff', fontWeight: 600, padding: '4px 10px', borderRadius: '6px', fontSize: '0.75rem' }}
        >
          Final Quote Decision
        </span>
      );
    }
    if (cat === 'negotiation_tradeoff_authorization' || meta?.type === 'negotiation_tradeoff_authorization') {
      return (
        <span 
          className="badge badge-flag business tradeoff-badge" 
          style={{ background: '#4338ca', color: '#ffffff', fontWeight: 600, padding: '4px 10px', borderRadius: '6px', fontSize: '0.75rem' }}
        >
          Trade-Off Authorization Required
        </span>
      );
    }
    switch (cat) {
      case 'requires_business_knowledge':
        return <span className="badge badge-flag business">Business Knowledge Required</span>;
      case 'contradictory_information':
        return <span className="badge badge-flag contradiction">Price / Term Contradiction</span>;
      case 'unclear_intent':
        return <span className="badge badge-flag unclear">Unclear Intent / Max Rounds</span>;
      default:
        return <span className="badge badge-flag other">Human Intervention Needed</span>;
    }
  };

  const pendingFlags = flags.filter((f) => f.status === 'pending');
  const resolvedFlags = flags.filter((f) => f.status === 'resolved');

  return (
    <div className="view-container">
      <div className="view-header">
        <div>
          <h2 className="view-title flex-items">
            <AlertTriangle size={24} className="text-amber" />
            <span>Agent Attention & Human Review</span>
          </h2>
          <p className="view-description">
            Tasks flagged by the AI agent requiring human procurement decisions (custom payment terms, intent ambiguity, or price contradictions).
          </p>
        </div>
      </div>

      {loading ? (
        <div className="loading-state">Loading pending review items...</div>
      ) : (
        <div className="flags-layout">
          {/* Pending Flags Section */}
          <div className="card">
            <div className="card-header flex-between">
              <h3 className="card-title">Pending Human Review ({pendingFlags.length})</h3>
              {pendingFlags.length > 0 && <span className="badge badge-status clarifying">Requires Action</span>}
            </div>

            {pendingFlags.length === 0 ? (
              <div className="empty-state">
                <CheckCircle2 size={44} className="success-icon" />
                <h3>All Agent Escalations Resolved</h3>
                <p>There are currently no pending tasks requiring human intervention.</p>
              </div>
            ) : (
              <div className="flagged-items-list">
                {pendingFlags.map((flag) => {
                  const suppName = flag.suppliers?.name || 'Supplier';
                  const phone = flag.suppliers?.phone_number || '';
                  const product = flag.rfqs?.product_name || '';

                  const isTradeoff = flag.category === 'negotiation_tradeoff_authorization' || flag.metadata?.type === 'negotiation_tradeoff_authorization';
                  const isFinalQuote = flag.category === 'supplier_final_quote_decision' || flag.metadata?.type === 'supplier_final_quote_decision';
                  const meta = flag.metadata || {};
                  const isCustomOpen = !!showCustomConfig[flag.id];
                  const customValues = customFormState[flag.id] || {};

                  return (
                    <div key={flag.id} className={`flagged-item-card ${isTradeoff ? 'tradeoff-flagged-card' : ''}`}>
                      <div className="flag-item-header">
                        <div>
                          <strong className={isTradeoff ? 'tradeoff-supplier-name' : ''}>{suppName}</strong>
                          {phone && (
                            <span className={isTradeoff ? 'tradeoff-phone' : 'phone-sub'}>
                              <Phone size={12} style={isTradeoff ? { color: '#64748b' } : {}} /> {phone}
                            </span>
                          )}
                          {product && (
                            <span className={isTradeoff ? 'badge tradeoff-product-chip' : 'badge badge-category'}>
                              {product}
                            </span>
                          )}
                        </div>
                        {getCategoryBadge(flag.category, meta)}
                      </div>

                      {isFinalQuote && (
                        <div className="tradeoff-proposal-box" style={{ borderColor: '#0f766e' }}>
                          <div className="tradeoff-proposal-title">
                            <ShieldCheck size={18} style={{ color: '#0f766e' }} />
                            <span>Final Supplier Quote</span>
                          </div>

                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.75rem', marginBottom: '0.85rem' }}>
                            <div className="tradeoff-metric-price">
                              <div className="tradeoff-metric-price-label">Final Price</div>
                              <div className="tradeoff-metric-price-value">AED {meta.supplier_final_price ?? '-'}</div>
                            </div>
                            <div className="tradeoff-metric-req">
                              <div className="tradeoff-metric-req-label">Delivery</div>
                              <div className="tradeoff-metric-req-value">{meta.delivery_time || 'Not specified'}</div>
                            </div>
                            <div className="tradeoff-metric-prop">
                              <div className="tradeoff-metric-prop-label">Buyer Range</div>
                              <div className="tradeoff-metric-prop-value">
                                AED {meta.preferred_target ?? '-'} target · AED {meta.acceptable_max ?? '-'} max
                              </div>
                            </div>
                          </div>

                          <div className="tradeoff-question">
                            <strong>Supplier says this is their final price.</strong> Choose what to do with this supplier. This does not close the whole RFQ.
                          </div>

                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginTop: '0.75rem' }}>
                            <button
                              type="button"
                              className="btn btn-primary btn-sm"
                              onClick={() => handleFinalQuoteDecision(flag, 'keep_for_evaluation')}
                              disabled={resolvingId === flag.id}
                            >
                              <Check size={14} />
                              Keep Quote for Evaluation
                            </button>

                            <button
                              type="button"
                              className="btn btn-sm tradeoff-btn-secondary"
                              onClick={() => handleFinalQuoteDecision(flag, 'end_supplier')}
                              disabled={resolvingId === flag.id}
                            >
                              <XCircle size={14} style={{ color: '#dc2626' }} />
                              End Negotiation With Supplier
                            </button>
                          </div>
                        </div>
                      )}

                      {/* Structured Trade-Off Section */}
                      {isTradeoff ? (
                        <div className="tradeoff-proposal-box">
                          <div className="tradeoff-proposal-title">
                            <Sliders size={18} style={{ color: '#4338ca' }} />
                            <span>Supplier Trade-Off Proposal</span>
                          </div>

                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.75rem', marginBottom: '0.85rem' }}>
                            <div className="tradeoff-metric-req">
                              <div className="tradeoff-metric-req-label">Current Requirement:</div>
                              <div className="tradeoff-metric-req-value">
                                {meta.dimension === 'delivery'
                                  ? `Delivery: ${meta.current_value != null ? `${meta.current_value} days` : 'Not specified'}`
                                  : meta.dimension === 'quantity'
                                  ? `Quantity: ${meta.current_value ?? 'Standard'}`
                                  : `Specs: Fixed`}
                              </div>
                            </div>

                            <div className="tradeoff-metric-prop">
                              <div className="tradeoff-metric-prop-label">Supplier Proposes:</div>
                              <div className="tradeoff-metric-prop-value">
                                {meta.dimension === 'delivery' ? `Delivery: ${meta.supplier_proposed_value ?? '-'} days` : meta.dimension === 'quantity' ? `Quantity: ${meta.supplier_proposed_value ?? '-'}` : `Specs: ${meta.supplier_proposed_value ?? '-'}`}
                              </div>
                            </div>

                            {meta.supplier_latest_price && (
                              <div className="tradeoff-metric-price">
                                <div className="tradeoff-metric-price-label">Supplier Quoted Price:</div>
                                <div className="tradeoff-metric-price-value">
                                  AED {meta.supplier_latest_price}
                                </div>
                              </div>
                            )}
                          </div>

                          <div className="tradeoff-question">
                            <strong>Question: </strong>
                            Can the AI negotiate using {meta.dimension === 'delivery' ? `delivery up to ${meta.supplier_proposed_value || 5} days` : meta.dimension === 'quantity' ? `quantity up to ${meta.supplier_proposed_value || 30}` : 'this alternative specification'}?
                          </div>

                          {/* Action Buttons */}
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.5rem' }}>
                            <button
                              type="button"
                              className="btn btn-primary btn-sm"
                              onClick={() => handleTradeoffDecision(flag, 'approve')}
                              disabled={resolvingId === flag.id}
                            >
                              <ShieldCheck size={14} />
                              <span>
                                {meta.dimension === 'delivery'
                                  ? `Approve up to ${meta.supplier_proposed_value || 5} days`
                                  : meta.dimension === 'quantity'
                                  ? `Approve up to ${meta.supplier_proposed_value || 30}`
                                  : 'Approve Alternative'}
                              </span>
                            </button>

                            <button
                              type="button"
                              className="btn btn-sm tradeoff-btn-secondary"
                              onClick={() => handleTradeoffDecision(flag, 'reject')}
                              disabled={resolvingId === flag.id}
                            >
                              <XCircle size={14} style={{ color: '#dc2626' }} />
                              <span>
                                {meta.dimension === 'delivery'
                                  ? `Keep ${meta.current_value || 2}-day requirement`
                                  : meta.dimension === 'quantity'
                                  ? `Keep ${meta.current_value || 20} qty requirement`
                                  : 'Keep original specs'}
                              </span>
                            </button>

                            <button
                              type="button"
                              className={`btn btn-sm tradeoff-btn-custom-toggle ${isCustomOpen ? 'active' : ''}`}
                              onClick={() => setShowCustomConfig((prev) => ({ ...prev, [flag.id]: !prev[flag.id] }))}
                              disabled={resolvingId === flag.id}
                            >
                              <Sliders size={14} style={{ color: '#4338ca' }} />
                              <span>{isCustomOpen ? 'Hide Custom Range' : 'Set Custom Range'}</span>
                            </button>
                          </div>

                          {/* Custom Range Drawer */}
                          {isCustomOpen && (
                            <div className="tradeoff-custom-drawer">
                              <div className="tradeoff-custom-drawer-title">Specify Custom Authority:</div>
                              {meta.dimension === 'delivery' ? (
                                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                                  <input
                                    type="number"
                                    className="input-field tradeoff-input"
                                    style={{ maxWidth: '160px' }}
                                    placeholder="Max days (e.g. 7)"
                                    value={customValues.max_days || ''}
                                    onChange={(e) => setCustomFormState((prev) => ({
                                      ...prev,
                                      [flag.id]: { ...prev[flag.id], max_days: e.target.value }
                                    }))}
                                  />
                                  <button
                                    type="button"
                                    className="btn btn-primary btn-sm"
                                    disabled={!customValues.max_days || Number(customValues.max_days) <= 0 || resolvingId === flag.id}
                                    onClick={() => handleTradeoffDecision(flag, 'approve', { max_days: Number(customValues.max_days) })}
                                  >
                                    Save & Resume
                                  </button>
                                </div>
                              ) : meta.dimension === 'quantity' ? (
                                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                                  <input
                                    type="number"
                                    className="input-field tradeoff-input"
                                    style={{ maxWidth: '120px' }}
                                    placeholder="Min qty"
                                    value={customValues.min || ''}
                                    onChange={(e) => setCustomFormState((prev) => ({
                                      ...prev,
                                      [flag.id]: { ...prev[flag.id], min: e.target.value }
                                    }))}
                                  />
                                  <input
                                    type="number"
                                    className="input-field tradeoff-input"
                                    style={{ maxWidth: '120px' }}
                                    placeholder="Max qty"
                                    value={customValues.max || ''}
                                    onChange={(e) => setCustomFormState((prev) => ({
                                      ...prev,
                                      [flag.id]: { ...prev[flag.id], max: e.target.value }
                                    }))}
                                  />
                                  <button
                                    type="button"
                                    className="btn btn-primary btn-sm"
                                    disabled={!customValues.min || !customValues.max || Number(customValues.min) > Number(customValues.max) || resolvingId === flag.id}
                                    onClick={() => handleTradeoffDecision(flag, 'approve', { min: Number(customValues.min), max: Number(customValues.max) })}
                                  >
                                    Save & Resume
                                  </button>
                                </div>
                              ) : (
                                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                                  <input
                                    type="text"
                                    className="input-field tradeoff-input"
                                    placeholder="Allowed substitute description"
                                    value={customValues.allowed_alternatives || ''}
                                    onChange={(e) => setCustomFormState((prev) => ({
                                      ...prev,
                                      [flag.id]: { ...prev[flag.id], allowed_alternatives: e.target.value }
                                    }))}
                                  />
                                  <button
                                    type="button"
                                    className="btn btn-primary btn-sm"
                                    disabled={!customValues.allowed_alternatives?.trim() || resolvingId === flag.id}
                                    onClick={() => handleTradeoffDecision(flag, 'approve', { allowed_alternatives: customValues.allowed_alternatives.trim() })}
                                  >
                                    Save & Resume
                                  </button>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="flag-reason-box">
                          <strong>Reason Flagged:</strong>
                          <p>{flag.reason}</p>
                        </div>
                      )}

                      <div className={isTradeoff ? 'flag-message-box tradeoff-message-box' : 'flag-message-box'}>
                        <strong className={isTradeoff ? 'tradeoff-message-title' : ''}>Raw WhatsApp Message Received:</strong>
                        <p className={isTradeoff ? 'raw-text tradeoff-message-text' : 'raw-text'}>"{flag.raw_message}"</p>
                      </div>

                      {/* Human Response Input Section */}
                      <div className={isTradeoff ? 'human-response-section tradeoff-response-section' : 'human-response-section'} style={{ marginTop: '0.85rem', paddingTop: '0.85rem' }}>
                        <label className={isTradeoff ? 'tradeoff-label' : 'form-label flex-items'} style={{ marginBottom: '0.4rem' }}>
                          <FileText size={14} /> <strong>Agent Instruction / Guidance:</strong>
                        </label>
                        <textarea
                          className={`input-field textarea-input ${isTradeoff ? 'tradeoff-textarea' : ''}`}
                          placeholder="Type instruction for AI agent to execute (e.g., 'We accept 50% advance against PI', 'Offer 48 AED')..."
                          rows={2}
                          value={responseTexts[flag.id] || ''}
                          onChange={(e) => setResponseTexts({ ...responseTexts, [flag.id]: e.target.value })}
                        />
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '0.5rem', flexWrap: 'wrap', gap: '0.5rem' }}>
                          <label className={isTradeoff ? 'tradeoff-checkbox-label' : ''} style={!isTradeoff ? { fontSize: '0.8rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' } : {}}>
                            <input
                              type="checkbox"
                              checked={sendToSupplierState[flag.id] !== false}
                              onChange={(e) => setSendToSupplierState({ ...sendToSupplierState, [flag.id]: e.target.checked })}
                            />
                            <span>Send agent-interpreted message to supplier via WhatsApp</span>
                          </label>

                          <div style={{ display: 'flex', gap: '0.5rem' }}>
                            <button
                              className={`btn btn-sm ${isTradeoff ? 'tradeoff-btn-secondary' : 'btn-secondary'}`}
                              onClick={() => handleResolve(flag.id)}
                              disabled={resolvingId === flag.id}
                            >
                              <Check size={14} style={isTradeoff ? { color: '#059669' } : {}} />
                              <span>{resolvingId === flag.id ? 'Resolving...' : 'Mark Resolved'}</span>
                            </button>
                            <button
                              className="btn btn-primary btn-sm"
                              onClick={() => handleRespond(flag.id)}
                              disabled={resolvingId === flag.id || !(responseTexts[flag.id] || '').trim()}
                            >
                              <Send size={14} />
                              <span>{resolvingId === flag.id ? 'Processing...' : 'Send Instruction & Resolve'}</span>
                            </button>
                          </div>
                        </div>
                      </div>

                      <div className="flag-item-footer" style={{ marginTop: '0.75rem' }}>
                        <span className={isTradeoff ? 'tradeoff-timestamp' : 'timestamp'}>
                          <Clock size={12} /> Flagged: {new Date(flag.created_at).toLocaleString()}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Resolved History Section */}
          {resolvedFlags.length > 0 && (
            <div className="card">
              <div className="card-header">
                <h4 className="card-title">Resolved Escalation History ({resolvedFlags.length})</h4>
              </div>
              <div className="resolved-list">
                {resolvedFlags.map((flag) => (
                  <div key={flag.id} className="resolved-item-row">
                    <div>
                      <strong>{flag.suppliers?.name || 'Supplier'}</strong>
                      <span className="notes-text"> — {flag.reason}</span>
                    </div>
                    <span className="timestamp">
                      Resolved at: {new Date(flag.resolved_at || flag.created_at).toLocaleDateString()}
                    </span>
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
