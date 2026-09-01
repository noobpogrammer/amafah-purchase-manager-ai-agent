import React, { useState, useEffect } from 'react';
import { fetchFlags, resolveFlag } from '../api';
import { AlertTriangle, CheckCircle2, Phone, Clock, FileText, Check } from 'lucide-react';

export default function AgentAttentionView({ refreshFlagsCount }) {
  const [flags, setFlags] = useState([]);
  const [loading, setLoading] = useState(true);
  const [resolvingId, setResolvingId] = useState(null);

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

  const getCategoryBadge = (cat) => {
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

                  return (
                    <div key={flag.id} className="flagged-item-card">
                      <div className="flag-item-header">
                        <div>
                          <strong>{suppName}</strong>
                          {phone && <span className="phone-sub"><Phone size={12} /> {phone}</span>}
                          {product && <span className="badge badge-category">{product}</span>}
                        </div>
                        {getCategoryBadge(flag.category)}
                      </div>

                      <div className="flag-reason-box">
                        <strong>Reason Flagged:</strong>
                        <p>{flag.reason}</p>
                      </div>

                      <div className="flag-message-box">
                        <strong>Raw WhatsApp Message Received:</strong>
                        <p className="raw-text">"{flag.raw_message}"</p>
                      </div>

                      <div className="flag-item-footer">
                        <span className="timestamp">
                          <Clock size={12} /> Flagged: {new Date(flag.created_at).toLocaleString()}
                        </span>

                        <button
                          className="btn btn-success btn-sm"
                          onClick={() => handleResolve(flag.id)}
                          disabled={resolvingId === flag.id}
                        >
                          <Check size={14} />
                          <span>{resolvingId === flag.id ? 'Resolving...' : 'Mark Resolved'}</span>
                        </button>
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
