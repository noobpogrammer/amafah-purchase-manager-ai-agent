import React, { useState, useEffect } from 'react';
import * as XLSX from 'xlsx';
import { Send, CheckCircle, AlertTriangle, ArrowRight, Tag, Clock, Package, FileText, Plus, Upload } from 'lucide-react';
import { createRFQ, fetchCategories, createCustomCategory, bulkCreateRFQs } from '../api';
import { DEMO_CLIENT_ID } from '../supabaseClient';

const DEFAULT_CATEGORIES = [
  'Electronics',
  'Hardware',
  'Plumbing',
  'Electrical',
  'Tools',
  'Building Materials',
  'General',
];

const normalizeBulkString = (v) => (typeof v === 'string' ? v.trim() : '');

export default function CreateRFQView({ onRFQCreated, setActiveTab, setSelectedRfqId }) {
  const [productName, setProductName] = useState('');
  const [categories, setCategories] = useState(DEFAULT_CATEGORIES);
  const [category, setCategory] = useState('Hardware');
  const [specs, setSpecs] = useState('');
  const [quantity, setQuantity] = useState('');
  const [lastQuote, setLastQuote] = useState('');
  const [deadlineHours, setDeadlineHours] = useState(24);

  const [showCustomCatInput, setShowCustomCatInput] = useState(false);
  const [customCatName, setCustomCatName] = useState('');
  const [creatingCat, setCreatingCat] = useState(false);

  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [matchedResult, setMatchedResult] = useState(null);

  const [isBulkMode, setIsBulkMode] = useState(false);
  const [bulkFile, setBulkFile] = useState(null);
  const [bulkRows, setBulkRows] = useState([]);
  const [bulkSubmitting, setBulkSubmitting] = useState(false);
  const [bulkSummary, setBulkSummary] = useState(null);

  const loadCategories = async () => {
    try {
      const list = await fetchCategories();
      if (list && list.length) {
        setCategories(list);
        if (!category || !list.includes(category)) {
          setCategory(list[0]);
        }
      }
    } catch (e) {
      console.error('Error loading categories:', e);
    }
  };

  useEffect(() => {
    loadCategories();
  }, []);

  const formHasRequiredFields =
    productName.trim() &&
    category && category.trim() &&
    specs.trim() &&
    String(deadlineHours).trim() !== '' &&
    Number(deadlineHours) > 0;

  const handleCategorySelectChange = (e) => {
    const val = e.target.value;
    if (val === '__CREATE_CUSTOM__') {
      setShowCustomCatInput(true);
      setCustomCatName('');
    } else {
      setCategory(val);
    }
  };

  const handleCreateCustomCategory = async (e) => {
    if (e) e.preventDefault();
    const clean = customCatName.trim();
    if (!clean) return;

    setCreatingCat(true);
    try {
      const created = await createCustomCategory(clean);
      if (!categories.includes(created)) {
        setCategories([...categories, created]);
      }
      setCategory(created);
      setCustomCatName('');
      setShowCustomCatInput(false);
    } catch (err) {
      console.error(err);
      setErrorMsg(err.message || 'Failed to create custom category');
    } finally {
      setCreatingCat(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!productName.trim()) {
      setErrorMsg('Product Name is required.');
      return;
    }
    if (!category || !category.trim()) {
      setErrorMsg('Supplier Category is required.');
      return;
    }
    if (!specs.trim()) {
      setErrorMsg('Specifications / Notes are required.');
      return;
    }
    if (!String(deadlineHours).trim() || Number(deadlineHours) <= 0) {
      setErrorMsg('Response Deadline (Hours) is required and must be greater than 0.');
      return;
    }

    setLoading(true);
    setErrorMsg('');
    setMatchedResult(null);

    try {
      const res = await createRFQ({
        product_name: productName.trim(),
        category: category.trim(),
        specs: specs.trim(),
        quantity,
        last_quote: lastQuote ? Number(lastQuote) : null,
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

  const normalizeSheetKey = (value) => String(value ?? '').trim().toLowerCase().replace(/[^a-z0-9]+/g, ' ');

  const getSheetValue = (row, aliases) => {
    for (const [rawKey, rawValue] of Object.entries(row || {})) {
      const normalized = normalizeSheetKey(rawKey);
      const compact = normalized.replace(/\s+/g, ' ');
      if (aliases.includes(normalized) || aliases.includes(compact)) {
        return rawValue;
      }
    }
    return '';
  };

  const handleBulkFileChange = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const ext = file.name.split('.').pop()?.toLowerCase();
    if (ext !== 'csv') {
      setErrorMsg('Please upload a CSV file (.csv).');
      return;
    }

    try {
      setBulkFile(file);
      setErrorMsg('');
      const data = await file.arrayBuffer();
      const workbook = XLSX.read(data, { type: 'array' });
      const sheet = workbook.Sheets[workbook.SheetNames[0]];
      const jsonRows = XLSX.utils.sheet_to_json(sheet, { defval: '' });

      const mappedRows = jsonRows
        .filter((row) => Object.values(row).some((v) => normalizeBulkString(v)))
        .map((row, idx) => {
          const descriptionRaw = getSheetValue(row, ['description', 'item description']);
          const description = normalizeBulkString(descriptionRaw);
          const cleanedProductName = description.replace(/\s+\d+\s*(pcs|pc|nos|bag|pkt)\s*$/i, '').trim();
          const qtyRaw = getSheetValue(row, ['qty', 'quantity']);
          const lastQuoteRaw = getSheetValue(row, ['last cost', 'last quote', 'last quotation', 'last qoute']);
          const rowNumberRaw = getSheetValue(row, ['sl', 'sl #', 'sl no', 'sl no.', 'sl no ']);

          let quantityValue = null;
          if (qtyRaw !== '' && qtyRaw !== null && qtyRaw !== undefined) {
            const asNumber = Number(String(qtyRaw).replace(/[^0-9.-]/g, ''));
            quantityValue = Number.isFinite(asNumber) ? asNumber : null;
          }

          let lastQuoteValue = null;
          if (lastQuoteRaw !== '' && lastQuoteRaw !== null && lastQuoteRaw !== undefined) {
            const asNumber = Number(String(lastQuoteRaw).replace(/[^0-9.-]/g, ''));
            lastQuoteValue = Number.isFinite(asNumber) ? asNumber : null;
          }

          return {
            id: `${idx + 1}`,
            rowNumber: rowNumberRaw || idx + 2,
            description,
            product_name: cleanedProductName || description,
            specs: (description.match(/\d+\s*(?:X|x)\s*\d+|\d+(?:\.\d+)?\s*(?:MM|CM|M|W|KW|V|A)/i)?.[0] || '').trim(),
            quantity: quantityValue,
            last_quote: lastQuoteValue,
            category: category || categories[0] || 'General',
            selected: true,
          };
        });

      setBulkRows(mappedRows);
      setBulkSummary(null);
      if (!mappedRows.length) {
        setErrorMsg('No usable rows were detected in the uploaded file.');
      }
    } catch (err) {
      console.error(err);
      setErrorMsg('Could not parse the CSV file. Please export it as CSV and try again.');
    }
  };

  const toggleBulkRow = (id) => {
    setBulkRows((prev) => prev.map((row) => row.id === id ? { ...row, selected: !row.selected } : row));
  };

  const updateBulkRowCategory = (id, value) => {
    setBulkRows((prev) => prev.map((row) => row.id === id ? { ...row, category: value } : row));
  };

  const submitBulkRows = async () => {
    const selectedRows = bulkRows.filter((row) => row.selected);
    if (!selectedRows.length) {
      setErrorMsg('Select at least one row before submitting the bulk RFQ import.');
      return;
    }

    if (!bulkFile) {
      setErrorMsg('Please upload a Material Requisition file first.');
      return;
    }

    const formData = new FormData();
    formData.append('file', bulkFile);
    formData.append('client_id', DEMO_CLIENT_ID);
    formData.append('category', selectedRows[0].category || category || 'General');
    formData.append('deadline_hours', String(deadlineHours || 24));
    formData.append('row_categories', JSON.stringify(selectedRows.map((row) => row.category || selectedRows[0].category || category || 'General')));

    setBulkSubmitting(true);
    setErrorMsg('');
    try {
      const result = await bulkCreateRFQs(formData);
      setBulkSummary(result);
      setBulkRows([]);
      setBulkFile(null);
      const input = document.getElementById('bulk-rfq-input');
      if (input) input.value = '';
      if (onRFQCreated) onRFQCreated();
    } catch (err) {
      console.error(err);
      setErrorMsg(err.message || 'Bulk RFQ upload failed.');
    } finally {
      setBulkSubmitting(false);
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
            Submit a single RFQ or upload a Material Requisition export in CSV format.
          </p>
        </div>
      </div>

      <div className="form-layout">
        <div className="card form-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
            <button type="button" className="btn btn-secondary" onClick={() => setIsBulkMode((prev) => !prev)}>
              <Upload size={16} />
              {isBulkMode ? 'Switch to Single RFQ' : 'Upload Material Requisition'}
            </button>
          </div>

          {!isBulkMode ? (
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
                  {showCustomCatInput ? (
                    <div className="custom-cat-inline-row" style={{ display: 'flex', gap: '0.4rem' }}>
                      <input
                        type="text"
                        className="input-field"
                        placeholder="Type custom category name..."
                        value={customCatName}
                        onChange={(e) => setCustomCatName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            handleCreateCustomCategory(e);
                          }
                        }}
                        autoFocus
                        style={{ flex: 1 }}
                      />
                      <button type="button" className="btn btn-primary btn-sm" onClick={handleCreateCustomCategory} disabled={creatingCat}>
                        {creatingCat ? 'Adding...' : 'Add'}
                      </button>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => {
                          setShowCustomCatInput(false);
                          setCustomCatName('');
                          if (categories.length > 0) setCategory(categories[0]);
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <select className="input-field" value={category} onChange={handleCategorySelectChange} required>
                      {categories.map((cat) => (
                        <option key={cat} value={cat}>{cat}</option>
                      ))}
                      <option value="__CREATE_CUSTOM__">+ Create Custom Category...</option>
                    </select>
                  )}
                  <span className="field-hint">Matching suppliers with '{category}' tag will receive this RFQ.</span>
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
                  {!String(deadlineHours).trim() || Number(deadlineHours) <= 0 ? (
                    <div className="error-text">Deadline is required.</div>
                  ) : null}
                </div>
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label className="form-label flex-items">
                    <FileText size={16} /> Specifications / Notes *
                  </label>
                  <input
                    type="text"
                    className="input-field"
                    placeholder="e.g. Type L, ASTM B88 compliant, 20ft lengths"
                    value={specs}
                    onChange={(e) => setSpecs(e.target.value)}
                    required
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

              <div className="form-group">
                <label className="form-label flex-items">Last Quote / Last Cost (AED)</label>
                <input
                  type="number"
                  className="input-field"
                  placeholder="e.g. 12.50"
                  value={lastQuote}
                  onChange={(e) => setLastQuote(e.target.value)}
                  step="0.01"
                />
              </div>

              <div className="form-actions">
                <button type="submit" className="btn btn-primary btn-lg" disabled={loading || !formHasRequiredFields}>
                  <Send size={18} />
                  <span>{loading ? 'Matching Suppliers & Queuing...' : 'Submit & Match Suppliers'}</span>
                </button>
              </div>
            </form>
          ) : (
            <div>
              <div className="form-group">
                <label className="form-label flex-items">
                  <Upload size={16} /> Upload Material Requisition (.csv)
                </label>
                <input id="bulk-rfq-input" type="file" accept=".csv" className="input-field" onChange={handleBulkFileChange} />
              </div>

              {bulkRows.length > 0 && (
                <div>
                  <div className="form-row" style={{ marginBottom: '0.75rem', alignItems: 'center' }}>
                    <div className="form-group" style={{ flex: 1 }}>
                      <label className="form-label flex-items">
                        <Tag size={16} /> Default Category
                      </label>
                      <select className="input-field" value={category} onChange={(e) => setCategory(e.target.value)}>
                        {categories.map((cat) => (
                          <option key={cat} value={cat}>{cat}</option>
                        ))}
                      </select>
                    </div>
                    <div className="form-group" style={{ flex: 1 }}>
                      <label className="form-label flex-items">
                        <Clock size={16} /> Default Deadline (Hours)
                      </label>
                      <input type="number" className="input-field" min="1" value={deadlineHours} onChange={(e) => setDeadlineHours(e.target.value)} />
                    </div>
                  </div>

                  <div style={{ overflowX: 'auto', marginBottom: '1rem' }}>
                    <table className="table table-responsive">
                      <thead>
                        <tr>
                          <th>Select</th>
                          <th>Row</th>
                          <th>Product Name</th>
                          <th>Specs</th>
                          <th>Qty</th>
                          <th>Last Quote</th>
                          <th>Category</th>
                        </tr>
                      </thead>
                      <tbody>
                        {bulkRows.map((row) => (
                          <tr key={row.id}>
                            <td><input type="checkbox" checked={row.selected} onChange={() => toggleBulkRow(row.id)} /></td>
                            <td>{row.rowNumber}</td>
                            <td>{row.product_name || '—'}</td>
                            <td>{row.specs || '—'}</td>
                            <td>{row.quantity ?? '—'}</td>
                            <td>{row.last_quote ?? '—'}</td>
                            <td>
                              <select value={row.category} onChange={(e) => updateBulkRowCategory(row.id, e.target.value)}>
                                {categories.map((cat) => (
                                  <option key={cat} value={cat}>{cat}</option>
                                ))}
                              </select>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="form-actions">
                    <button type="button" className="btn btn-primary" onClick={submitBulkRows} disabled={bulkSubmitting}>
                      {bulkSubmitting ? 'Submitting RFQs...' : 'Confirm Bulk Upload'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {bulkSummary && (
          <div className="card result-card">
            <div className="success-banner">
              <CheckCircle size={28} className="banner-icon" />
              <div>
                <h3 className="banner-title">Bulk RFQs Created</h3>
                <p className="banner-subtitle">
                  Created <strong>{bulkSummary.created_count}</strong> RFQs. Review the summary below.
                </p>
              </div>
            </div>
            <ul>
              {bulkSummary.rfqs?.map((rfq) => (
                <li key={rfq.rfq_id}>
                  {rfq.product_name} — {rfq.matched_suppliers_count} matched suppliers
                </li>
              ))}
            </ul>
            {bulkSummary.failed_rows?.length > 0 && (
              <div>
                <h4>Failed rows</h4>
                <ul>
                  {bulkSummary.failed_rows.map((row) => (
                    <li key={`${row.row_number}-${row.reason}`}>Row {row.row_number}: {row.reason}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

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
                  <button className="btn btn-primary" onClick={() => handleGoToTracking(matchedResult.rfq_id)}>
                    Track Live RFQ Status <ArrowRight size={16} />
                  </button>
                </div>
              </div>
            ) : (
              <div className="warning-banner-box">
                <AlertTriangle size={28} className="banner-icon warning-icon" />
                <div>
                  <h3 className="banner-title">No Matching Suppliers Found</h3>
                  <p className="banner-subtitle">{matchedResult.message || `No active suppliers were found with category '${category}'.`}</p>
                  <button className="btn btn-secondary btn-sm" onClick={() => setActiveTab('suppliers')}>Add Category Suppliers</button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
