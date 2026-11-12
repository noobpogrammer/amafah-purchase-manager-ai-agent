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
          <h2 className="view-title">Procurement overview</h2>
          <p className="view-description">
            Your buying requests, supplier quotes and decisions, all in one place.
          </p>
        </div>
        <div className="action-button-group">
          <button className="btn btn-primary" onClick={() => setActiveTab('create_rfq')}>
            <PlusCircle size={18} />
            <span>Create request</span>
          </button>
        </div>
      </div>

      {pendingFlags > 0 && (
        <div className="alert-banner warning-banner">
          <div className="alert-content">
            <AlertTriangle size={20} className="alert-icon" />
            <div>
              <strong>{pendingFlags} item{pendingFlags > 1 ? 's' : ''} {pendingFlags > 1 ? 'need' : 'needs'} your attention</strong>
              <p>A few items need your decision before the agent can continue.</p>
            </div>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={() => setActiveTab('flags')}>
            Review Items <ArrowRight size={14} />
          </button>
        </div>
      )}

      {/* Metric Grid */}
      <div className="metrics-grid">
        <button type="button" className="metric-card" onClick={() => setActiveTab('rfqs')}>
          <div className="metric-header">
            <span className="metric-label">Active RFQs</span>
            <div className="metric-icon-bg icon-blue"><FileText size={20} /></div>
          </div>
          <div className="metric-value">{activeRfqs}</div>
          <div className="metric-footer">{totalRfqs} total RFQs created</div>
        </button>

        <button type="button" className="metric-card" onClick={() => setActiveTab('suppliers')}>
          <div className="metric-header">
            <span className="metric-label">Supplier network</span>
            <div className="metric-icon-bg icon-green"><Users size={20} /></div>
          </div>
          <div className="metric-value">{totalSuppliers}</div>
          <div className="metric-footer">Ready to match with new requests</div>
        </button>

        <button type="button" className="metric-card" onClick={() => setActiveTab('quotes_report')}>
          <div className="metric-header">
            <span className="metric-label">Quotes Received</span>
            <div className="metric-icon-bg icon-purple"><CheckCircle2 size={20} /></div>
          </div>
          <div className="metric-value">{totalQuotes}</div>
          <div className="metric-footer">Offers across your buying requests</div>
        </button>

        <button type="button" className="metric-card" onClick={() => setActiveTab('flags')}>
          <div className="metric-header">
            <span className="metric-label">Needs attention</span>
            <div className="metric-icon-bg icon-amber"><AlertTriangle size={20} /></div>
          </div>
          <div className="metric-value">{pendingFlags}</div>
          <div className="metric-footer">Decisions waiting for your review</div>
        </button>
      </div>

      {/* Quick Action Navigation Panels */}
      <div className="dashboard-grid">
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Your next steps</h3>
          </div>
          <div className="workflow-steps">
            <button type="button" className="workflow-step" onClick={() => setActiveTab('suppliers')}>
              <div className="step-number">1</div>
              <div className="step-info">
                <strong>Manage your suppliers</strong>
                <p>Organize contacts and categories for the right match.</p>
              </div>
              <ArrowRight size={16} />
            </button>

            <button type="button" className="workflow-step" onClick={() => setActiveTab('create_rfq')}>
              <div className="step-number">2</div>
              <div className="step-info">
                <strong>Send a buying request</strong>
                <p>Set your requirements and invite matched suppliers.</p>
              </div>
              <ArrowRight size={16} />
            </button>

            <button type="button" className="workflow-step" onClick={() => setActiveTab('rfqs')}>
              <div className="step-number">3</div>
              <div className="step-info">
                <strong>Follow each request</strong>
                <p>See replies, quotations and outstanding questions.</p>
              </div>
              <ArrowRight size={16} />
            </button>

            <button type="button" className="workflow-step" onClick={() => setActiveTab('quotes_report')}>
              <div className="step-number">4</div>
              <div className="step-info">
                <strong>Compare your offers</strong>
                <p>Review prices, delivery and supplier recommendations.</p>
              </div>
              <ArrowRight size={16} />
            </button>
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
              View conversations
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
