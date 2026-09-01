import React from 'react';
import {
  FileText,
  Users,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  PlusCircle,
  MessageSquare,
  Clock
} from 'lucide-react';

export default function DashboardView({ metrics, loading, setActiveTab, setSelectedRfqId }) {
  if (loading) {
    return <div className="loading-state">Loading procurement dashboard metrics...</div>;
  }

  const {
    activeRfqs = 0,
    totalRfqs = 0,
    totalSuppliers = 0,
    totalQuotes = 0,
    pendingFlags = 0,
    recentMessages = []
  } = metrics || {};

  return (
    <div className="view-container">
      <div className="view-header">
        <div>
          <h2 className="view-title">Procurement Command Center</h2>
          <p className="view-description">
            Live overview of active RFQs, supplier response tracking, AI quote ranking, and human escalation tasks.
          </p>
        </div>
        <div className="action-button-group">
          <button className="btn btn-primary" onClick={() => setActiveTab('create_rfq')}>
            <PlusCircle size={18} />
            <span>Create New RFQ</span>
          </button>
        </div>
      </div>

      {pendingFlags > 0 && (
        <div className="alert-banner warning-banner">
          <div className="alert-content">
            <AlertTriangle size={20} className="alert-icon" />
            <div>
              <strong>{pendingFlags} Action Item{pendingFlags > 1 ? 's' : ''} Require Human Review</strong>
              <p>The AI agent encountered queries or contradictions requiring procurement decision.</p>
            </div>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={() => setActiveTab('flags')}>
            Review Items <ArrowRight size={14} />
          </button>
        </div>
      )}

      {/* Metric Grid */}
      <div className="metrics-grid">
        <div className="metric-card" onClick={() => setActiveTab('rfqs')}>
          <div className="metric-header">
            <span className="metric-label">Active RFQs</span>
            <div className="metric-icon-bg icon-blue"><FileText size={20} /></div>
          </div>
          <div className="metric-value">{activeRfqs}</div>
          <div className="metric-footer">{totalRfqs} total RFQs created</div>
        </div>

        <div className="metric-card" onClick={() => setActiveTab('suppliers')}>
          <div className="metric-header">
            <span className="metric-label">Registered Suppliers</span>
            <div className="metric-icon-bg icon-green"><Users size={20} /></div>
          </div>
          <div className="metric-value">{totalSuppliers}</div>
          <div className="metric-footer">Categorized for auto-matching</div>
        </div>

        <div className="metric-card" onClick={() => setActiveTab('quotes_report')}>
          <div className="metric-header">
            <span className="metric-label">Quotes Received</span>
            <div className="metric-icon-bg icon-purple"><CheckCircle2 size={20} /></div>
          </div>
          <div className="metric-value">{totalQuotes}</div>
          <div className="metric-footer">AI recorded quotes across RFQs</div>
        </div>

        <div className="metric-card" onClick={() => setActiveTab('flags')}>
          <div className="metric-header">
            <span className="metric-label">Pending Agent Flags</span>
            <div className="metric-icon-bg icon-amber"><AlertTriangle size={20} /></div>
          </div>
          <div className="metric-value">{pendingFlags}</div>
          <div className="metric-footer">Human-in-the-loop tasks</div>
        </div>
      </div>

      {/* Quick Action Navigation Panels */}
      <div className="dashboard-grid">
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Demo Flow Shortcuts</h3>
          </div>
          <div className="workflow-steps">
            <div className="workflow-step" onClick={() => setActiveTab('suppliers')}>
              <div className="step-number">1</div>
              <div className="step-info">
                <strong>Manage Suppliers</strong>
                <p>Register suppliers with multi-category tags (Electronics, Hardware, etc.)</p>
              </div>
              <ArrowRight size={16} />
            </div>

            <div className="workflow-step" onClick={() => setActiveTab('create_rfq')}>
              <div className="step-number">2</div>
              <div className="step-info">
                <strong>Launch RFQ & Auto-Match</strong>
                <p>Submit product category RFQ; auto-match suppliers & queue WhatsApp messages</p>
              </div>
              <ArrowRight size={16} />
            </div>

            <div className="workflow-step" onClick={() => setActiveTab('rfqs')}>
              <div className="step-number">3</div>
              <div className="step-info">
                <strong>Track Live Status</strong>
                <p>Watch supplier statuses move from 'sent' to 'responded' / 'clarifying'</p>
              </div>
              <ArrowRight size={16} />
            </div>

            <div className="workflow-step" onClick={() => setActiveTab('quotes_report')}>
              <div className="step-number">4</div>
              <div className="step-info">
                <strong>AI Quote Ranking Report</strong>
                <p>View AI comparative analysis, recommended best supplier, and price evaluation</p>
              </div>
              <ArrowRight size={16} />
            </div>
          </div>
        </div>

        {/* Live Message Log Activity */}
        <div className="card">
          <div className="card-header flex-between">
            <h3 className="card-title flex-items">
              <MessageSquare size={18} className="text-primary" />
              <span>Recent WhatsApp Activity</span>
            </h3>
            <button className="btn btn-text btn-sm" onClick={() => setActiveTab('conversations')}>
              View All Logs
            </button>
          </div>
          <div className="activity-feed">
            {recentMessages.length === 0 ? (
              <p className="empty-text">No WhatsApp activity logged yet.</p>
            ) : (
              recentMessages.map((msg) => (
                <div key={msg.id} className="activity-item">
                  <span className={`direction-badge ${msg.direction}`}>
                    {msg.direction === 'inbound' ? 'IN' : 'OUT'}
                  </span>
                  <div className="activity-details">
                    <div className="activity-meta">
                      <strong>{msg.suppliers?.name || 'Supplier'}</strong>
                      <span className="timestamp">
                        <Clock size={12} />
                        {new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>
                    <p className="activity-body">{msg.body}</p>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
