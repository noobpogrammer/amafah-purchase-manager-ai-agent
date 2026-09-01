import React, { useState } from 'react';
import { Send, CheckCircle, AlertTriangle, ArrowRight, Tag, Clock, Package, FileText } from 'lucide-react';
import { createRFQ } from '../api';

const CATEGORIES = [
  'Electronics',
  'Hardware',
  'Plumbing',
  'Electrical',
  'Tools',
  'Building Materials',
  'General',
];

export default function CreateRFQView({ onRFQCreated, setActiveTab, setSelectedRfqId }) {
  const [productName, setProductName] = useState('');
  const [category, setCategory] = useState('Hardware');
  const [specs, setSpecs] = useState('');
  const [quantity, setQuantity] = useState('');
  const [deadlineHours, setDeadlineHours] = useState(24);

  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [matchedResult, setMatchedResult] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!productName.trim()) {
      setErrorMsg('Product Name is required.');
      return;
    }

    setLoading(true);
    setErrorMsg('');
    setMatchedResult(null);

    try {
      const res = await createRFQ({
        product_name: productName,
        category,
        specs,
        quantity,
        deadline_hours: deadlineHours,
      });

      setMatchedResult(res);
      if (onRFQCreated) onRFQCreated();
    } catch (err) {
      console.error(err);
      setErrorMsg(err.message || 'Failed to submit RFQ');
    } finally {
      setLoading(false);
    }
  };

  const handleGoToTracking = (rfqId) => {
    if (setSelectedRfqId) setSelectedRfqId(rfqId);
    if (setActiveTab) setActiveTab('rfqs');
  };

  return (
    <div className="view-container">
      <div className="view-header">
        <div>
          <h2 className="view-title">Launch Request for Quote (RFQ)</h2>
          <p className="view-description">
            Submit product requirements. The AI agent auto-matches suppliers by category and queues WhatsApp dispatches with pacing.
          </p>
        </div>
      </div>

      <div className="form-layout">
        <div className="card form-card">
          <form onSubmit={handleSubmit}>
            {errorMsg && <div className="error-alert">{errorMsg}</div>}

            <div className="form-group">
              <label className="form-label flex-items">
                <Package size={16} /> Product Name *
              </label>
              <input
                type="text"
                className="input-field"
                placeholder="e.g. Copper Water Pipe 1/2 Inch"
                value={productName}
                onChange={(e) => setProductName(e.target.value)}
                required
              />
            </div>

            <div className="form-row">
              <div className="form-group">
                <label className="form-label flex-items">
                  <Tag size={16} /> Supplier Category *
                </label>
                <select
                  className="input-field"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  required
                >
                  {CATEGORIES.map((cat) => (
                    <option key={cat} value={cat}>
                      {cat}
                    </option>
                  ))}
                </select>
                <span className="field-hint">
                  Matching suppliers with '{category}' tag will receive this RFQ.
                </span>
              </div>

              <div className="form-group">
                <label className="form-label flex-items">
                  <Clock size={16} /> Response Deadline (Hours) *
                </label>
                <input
                  type="number"
                  className="input-field"
                  min="1"
                  max="168"
                  value={deadlineHours}
                  onChange={(e) => setDeadlineHours(e.target.value)}
                  required
                />
              </div>
            </div>

            <div className="form-row">
              <div className="form-group">
                <label className="form-label flex-items">
                  <FileText size={16} /> Specifications / Notes
                </label>
                <input
                  type="text"
                  className="input-field"
                  placeholder="e.g. Type L, ASTM B88 compliant, 20ft lengths"
                  value={specs}
                  onChange={(e) => setSpecs(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label className="form-label flex-items">Quantity (Units)</label>
                <input
                  type="number"
                  className="input-field"
                  placeholder="e.g. 50"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                />
              </div>
            </div>

            <div className="form-actions">
              <button type="submit" className="btn btn-primary btn-lg" disabled={loading}>
                <Send size={18} />
                <span>{loading ? 'Matching Suppliers & Queuing...' : 'Submit & Match Suppliers'}</span>
              </button>
            </div>
          </form>
        </div>

        {/* Live Submission Result Panel */}
        {matchedResult && (
          <div className="card result-card">
            {matchedResult.status === 'success' ? (
              <div>
                <div className="success-banner">
                  <CheckCircle size={28} className="banner-icon" />
                  <div>
                    <h3 className="banner-title">RFQ Created & Suppliers Matched</h3>
                    <p className="banner-subtitle">
                      Matched <strong>{matchedResult.matched_suppliers_count} supplier(s)</strong> in category '{category}'. Outbound WhatsApp messages have been pushed to the pacing queue.
                    </p>
                  </div>
                </div>

                <div className="matched-supplier-list">
                  <h4>Contacted Suppliers:</h4>
                  {matchedResult.suppliers.map((s) => (
                    <div key={s.id} className="supplier-matched-chip">
                      <div>
                        <strong>{s.name}</strong>
                        <span className="phone-sub">{s.phone}</span>
                      </div>
                      <span className="badge badge-status sent">Message Queued</span>
                    </div>
                  ))}
                </div>

                <div className="result-actions">
                  <button
                    className="btn btn-primary"
                    onClick={() => handleGoToTracking(matchedResult.rfq_id)}
                  >
                    Track Live RFQ Status <ArrowRight size={16} />
                  </button>
                </div>
              </div>
            ) : (
              <div className="warning-banner-box">
                <AlertTriangle size={28} className="banner-icon warning-icon" />
                <div>
                  <h3 className="banner-title">No Matching Suppliers Found</h3>
                  <p className="banner-subtitle">
                    {matchedResult.message || `No active suppliers were found with category '${category}'.`}
                  </p>
                  <button className="btn btn-secondary btn-sm" onClick={() => setActiveTab('suppliers')}>
                    Add Category Suppliers
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
