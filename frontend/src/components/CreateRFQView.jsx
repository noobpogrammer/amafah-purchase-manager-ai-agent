import React, { useState, useEffect } from 'react';
import * as XLSX from 'xlsx';
import { Send, CheckCircle, AlertTriangle, ArrowRight, Tag, Clock, Package, FileText, Upload, ChevronDown, ChevronRight, Sliders } from 'lucide-react';
import { createRFQ, fetchCategories, createCustomCategory, bulkCreateRFQs, fetchSuppliers } from '../api';
// client_id is derived server-side from authenticated user; do not import DEMO_CLIENT_ID

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
  const [category, setCategory] = useState('Hardware'); // bulk-mode default
  const [selectedCategories, setSelectedCategories] = useState(['Hardware']);
  const [targetingMode, setTargetingMode] = useState('category');
  const [availableSuppliers, setAvailableSuppliers] = useState([]);
  const [selectedSupplierIds, setSelectedSupplierIds] = useState([]);
  const [supplierSearch, setSupplierSearch] = useState('');
  const [specs, setSpecs] = useState('');
  const [quantity, setQuantity] = useState('');
  const [lastQuote, setLastQuote] = useState('');
  const [acceptablePriceMin, setAcceptablePriceMin] = useState('');
  const [acceptablePriceMax, setAcceptablePriceMax] = useState('');
  const [deadlineHours, setDeadlineHours] = useState(24);
  const [requiredDeliveryDays, setRequiredDeliveryDays] = useState('');

  // Negotiation Flexibility (Collapsible)
  const [showFlexibility, setShowFlexibility] = useState(false);
  const [allowQtyFlex, setAllowQtyFlex] = useState(false);
  const [qtyMin, setQtyMin] = useState('');
  const [qtyMax, setQtyMax] = useState('');
  const [allowDeliveryFlex, setAllowDeliveryFlex] = useState(false);
  const [deliveryMaxDays, setDeliveryMaxDays] = useState('');
  const [allowSpecFlex, setAllowSpecFlex] = useState(false);
  const [allowedSpecs, setAllowedSpecs] = useState('');

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
      const usable = list && list.length ? list : DEFAULT_CATEGORIES;
      setCategories(usable);
      if (!category || !usable.includes(category)) {
        setCategory(usable[0] || '');
      }
      setSelectedCategories((current) => {
        const kept = (current || []).filter((cat) => usable.includes(cat));
        return kept.length ? kept : (usable[0] ? [usable[0]] : []);
      });
    } catch (e) {
      console.error('Error loading categories:', e);
    }
  };

  const loadSuppliers = async () => {
    try {
      const list = await fetchSuppliers();
      setAvailableSuppliers((list || []).filter((s) => s.is_active !== false));
    } catch (e) {
      console.error('Error loading suppliers:', e);
    }
  };

  useEffect(() => {
    loadCategories();
    loadSuppliers();
  }, []);

  const formHasRequiredFields =
    productName.trim() &&
    selectedCategories.length > 0 &&
    specs.trim() &&
    String(deadlineHours).trim() !== '' &&
    Number(deadlineHours) > 0;

  const toggleRfqCategory = (cat) => {
    setSelectedCategories((current) =>
      current.includes(cat)
        ? current.filter((c) => c !== cat)
        : [...current, cat]
    );
  };

  const toggleTargetSupplier = (supplierId) => {
    setSelectedSupplierIds((current) =>
      current.includes(supplierId)
        ? current.filter((id) => id !== supplierId)
        : [...current, supplierId]
    );
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
      setSelectedCategories((current) => current.includes(created) ? current : [...current, created]);
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
    if (selectedCategories.length === 0) {
      setErrorMsg('Select at least one supplier category.');
      return;
    }
    if (targetingMode === 'selected' && selectedSupplierIds.length === 0) {
      setErrorMsg('Select at least one supplier, or switch targeting to all category matches.');
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

    if (acceptablePriceMin && Number(acceptablePriceMin) <= 0) {
      setErrorMsg('Acceptable Price Min must be a positive number.');
      return;
    }
    if (acceptablePriceMax && Number(acceptablePriceMax) <= 0) {
      setErrorMsg('Acceptable Price Max must be a positive number.');
      return;
    }
    if (acceptablePriceMin && acceptablePriceMax && Number(acceptablePriceMin) > Number(acceptablePriceMax)) {
      setErrorMsg('Acceptable Price Min cannot be greater than Acceptable Price Max.');
      return;
    }

    if (requiredDeliveryDays && Number(requiredDeliveryDays) <= 0) {
      setErrorMsg('Required Delivery (Days) must be a positive number.');
      return;
    }

    if (allowQtyFlex) {
      if (!qtyMin && !qtyMax) {
        setErrorMsg('Please specify at least a minimum or maximum quantity for flexibility.');
        return;
      }
      if (qtyMin && Number(qtyMin) <= 0) {
        setErrorMsg('Minimum quantity must be a positive number.');
        return;
      }
      if (qtyMax && Number(qtyMax) <= 0) {
        setErrorMsg('Maximum quantity must be a positive number.');
        return;
      }
      if (qtyMin && qtyMax && Number(qtyMin) > Number(qtyMax)) {
        setErrorMsg('Minimum quantity cannot be greater than maximum quantity.');
        return;
      }
    }

    if (allowDeliveryFlex) {
      if (!deliveryMaxDays || Number(deliveryMaxDays) <= 0) {
        setErrorMsg('Maximum acceptable delivery days must be greater than 0.');
        return;
      }
    }

    if (allowSpecFlex) {
      if (!allowedSpecs.trim()) {
        setErrorMsg('Please describe the explicitly allowed specification alternatives.');
        return;
      }
    }

    const flexibility = {};
    if (allowQtyFlex) {
      flexibility.quantity = {
        authorized: true,
        min: qtyMin ? Number(qtyMin) : null,
        max: qtyMax ? Number(qtyMax) : null,
      };
    }
    if (allowDeliveryFlex) {
      flexibility.delivery = {
        authorized: true,
        max_days: Number(deliveryMaxDays),
      };
    }
    if (allowSpecFlex) {
      flexibility.specification = {
        authorized: true,
        allowed_alternatives: allowedSpecs.trim(),
      };
    }

    setLoading(true);
    setErrorMsg('');
    setMatchedResult(null);

    try {
      const res = await createRFQ({
        product_name: productName.trim(),
        category: selectedCategories[0],
        categories: selectedCategories,
        targeting_mode: targetingMode,
        supplier_ids: targetingMode === 'selected' ? selectedSupplierIds : [],
        specs: specs.trim(),
        quantity,
        last_quote: lastQuote ? Number(lastQuote) : null,
        acceptable_price_min: acceptablePriceMin ? Number(acceptablePriceMin) : null,
        acceptable_price_max: acceptablePriceMax ? Number(acceptablePriceMax) : null,
        deadline_hours: deadlineHours,
        required_delivery_days: requiredDeliveryDays ? Number(requiredDeliveryDays) : null,
        flexibility: Object.keys(flexibility).length > 0 ? flexibility : null,
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
          const preferredTargetRaw = getSheetValue(row, [
            'preferred target price',
            'preferred target',
            'target price',
            'acceptable price min',
            'absolute minimum',
            'minimum price',
            'min price',
          ]);
          const maximumAcceptableRaw = getSheetValue(row, [
            'maximum acceptable price',
            'maximum acceptable',
            'acceptable price max',
            'absolute maximum',
            'maximum price',
            'max price',
          ]);
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

          const parseOptionalPrice = (rawValue) => {
            if (rawValue === '' || rawValue === null || rawValue === undefined) return null;
            const parsed = Number(String(rawValue).replace(/[^0-9.-]/g, ''));
            return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
          };

          const preferredTargetValue = parseOptionalPrice(preferredTargetRaw);
          const maximumAcceptableValue = parseOptionalPrice(maximumAcceptableRaw);

          return {
            id: `${idx + 1}`,
            rowNumber: rowNumberRaw || idx + 2,
            description,
            product_name: cleanedProductName || description,
            specs: (description.match(/\d+\s*(?:X|x)\s*\d+|\d+(?:\.\d+)?\s*(?:MM|CM|M|W|KW|V|A)/i)?.[0] || '').trim(),
            quantity: quantityValue,
            last_quote: lastQuoteValue,
            acceptable_price_min: preferredTargetValue,
            acceptable_price_max: maximumAcceptableValue,
            category: category || categories[0] || 'General',
            deadline_hours: deadlineHours,
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

  const updateBulkRowField = (id, field, value) => {
    setBulkRows((prev) => prev.map((row) => {
      if (row.id !== id) return row;
      const copy = { ...row };
      if (
        field === 'quantity'
        || field === 'deadline_hours'
        || field === 'last_quote'
        || field === 'acceptable_price_min'
        || field === 'acceptable_price_max'
      ) {
        const n = value === '' || value === null ? null : Number(String(value).replace(/[^0-9.-]/g, ''));
        copy[field] = Number.isFinite(n) ? n : null;
      } else {
        copy[field] = value;
      }
      return copy;
    }));
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

    for (const row of selectedRows) {
      const target = row.acceptable_price_min;
      const max = row.acceptable_price_max;

      if (target !== null && target !== undefined && Number(target) <= 0) {
        setErrorMsg(`Row ${row.rowNumber}: Preferred Target Price must be greater than 0.`);
        return;
      }
      if (max !== null && max !== undefined && Number(max) <= 0) {
        setErrorMsg(`Row ${row.rowNumber}: Maximum Acceptable Price must be greater than 0.`);
        return;
      }
      if (
        target !== null && target !== undefined
        && max !== null && max !== undefined
        && Number(target) > Number(max)
      ) {
        setErrorMsg(`Row ${row.rowNumber}: Preferred Target Price cannot be greater than Maximum Acceptable Price.`);
        return;
      }
    }

    const formData = new FormData();
    formData.append('file', bulkFile);
    formData.append('category', selectedRows[0].category || category || 'General');
    formData.append('deadline_hours', String(deadlineHours || 24));
    formData.append('row_categories', JSON.stringify(selectedRows.map((row) => row.category || selectedRows[0].category || category || 'General')));
    const rowUpdates = selectedRows.map((row) => ({
      product_name: row.product_name,
      quantity: row.quantity,
      category: row.category,
      deadline_hours: row.deadline_hours,
      specs: row.specs,
      last_quote: row.last_quote,
      acceptable_price_min: row.acceptable_price_min,
      acceptable_price_max: row.acceptable_price_max,
    }));
    formData.append('row_updates', JSON.stringify(rowUpdates));

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
                    <Tag size={16} /> Supplier Categories *
                  </label>
                  <div
                    style={{
                      display: 'flex',
                      flexWrap: 'wrap',
                      gap: '0.45rem',
                      padding: '0.55rem',
                      border: '1px solid var(--border-color, #d1d5db)',
                      borderRadius: '8px',
                      maxHeight: '150px',
                      overflowY: 'auto'
                    }}
                  >
                    {categories.map((cat) => {
                      const checked = selectedCategories.includes(cat);
                      return (
                        <label
                          key={cat}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '0.35rem',
                            padding: '0.35rem 0.55rem',
                            border: '1px solid var(--border-color, #d1d5db)',
                            borderRadius: '999px',
                            cursor: 'pointer'
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleRfqCategory(cat)}
                          />
                          <span>{cat}</span>
                        </label>
                      );
                    })}
                  </div>

                  {showCustomCatInput ? (
                    <div className="custom-cat-inline-row" style={{ display: 'flex', gap: '0.4rem', marginTop: '0.5rem' }}>
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
                      <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setShowCustomCatInput(false); setCustomCatName(''); }}>
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowCustomCatInput(true)} style={{ marginTop: '0.4rem' }}>
                      + Create Custom Category
                    </button>
                  )}
                  <span className="field-hint">
                    Category matching uses ANY selected category. Suppliers are deduplicated automatically.
                  </span>
                </div>

                <div className="form-group">
                  <label className="form-label">Send RFQ To</label>
                  <select
                    className="input-field"
                    value={targetingMode}
                    onChange={(e) => setTargetingMode(e.target.value)}
                  >
                    <option value="category">All suppliers matching selected categories</option>
                    <option value="selected">Specific supplier(s)</option>
                  </select>

                  {targetingMode === 'selected' && (
                    <div style={{ marginTop: '0.55rem' }}>
                      <input
                        type="search"
                        className="input-field"
                        placeholder="Search suppliers..."
                        value={supplierSearch}
                        onChange={(e) => setSupplierSearch(e.target.value)}
                      />
                      <div
                        style={{
                          marginTop: '0.4rem',
                          maxHeight: '170px',
                          overflowY: 'auto',
                          border: '1px solid var(--border-color, #d1d5db)',
                          borderRadius: '8px',
                          padding: '0.35rem'
                        }}
                      >
                        {availableSuppliers
                          .filter((s) => {
                            const q = supplierSearch.trim().toLowerCase();
                            return !q || (s.name || '').toLowerCase().includes(q) || (s.phone_number || '').includes(q);
                          })
                          .map((supplier) => (
                            <label
                              key={supplier.id}
                              style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', padding: '0.4rem', cursor: 'pointer' }}
                            >
                              <input
                                type="checkbox"
                                checked={selectedSupplierIds.includes(supplier.id)}
                                onChange={() => toggleTargetSupplier(supplier.id)}
                              />
                              <span>
                                <strong>{supplier.name}</strong>
                                <span className="phone-sub" style={{ marginLeft: '0.4rem' }}>{supplier.phone_number}</span>
                              </span>
                            </label>
                          ))}
                      </div>
                      <span className="field-hint">{selectedSupplierIds.length} supplier(s) selected.</span>
                    </div>
                  )}
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

              <div className="form-row">
                <div className="form-group">
                  <label className="form-label flex-items">
                    <Clock size={16} /> Required Delivery (Days)
                  </label>
                  <input
                    type="number"
                    className="input-field"
                    placeholder="e.g. 2 (Leave empty if not specified)"
                    min="1"
                    value={requiredDeliveryDays}
                    onChange={(e) => setRequiredDeliveryDays(e.target.value)}
                  />
                  <span className="field-hint">Optional baseline delivery timeline.</span>
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
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label className="form-label flex-items">Acceptable Price Min (AED)</label>
                  <input
                    type="number"
                    className="input-field"
                    placeholder="e.g. 50.00"
                    value={acceptablePriceMin}
                    onChange={(e) => setAcceptablePriceMin(e.target.value)}
                    step="0.01"
                  />
                </div>

                <div className="form-group">
                  <label className="form-label flex-items">Acceptable Price Max (AED)</label>
                  <input
                    type="number"
                    className="input-field"
                    placeholder="e.g. 60.00"
                    value={acceptablePriceMax}
                    onChange={(e) => setAcceptablePriceMax(e.target.value)}
                    step="0.01"
                  />
                </div>
              </div>

              {/* Optional Collapsible Negotiation Flexibility Section */}
              <div style={{ marginTop: '1.25rem', marginBottom: '1.25rem', border: '1px solid var(--border-color, #e2e8f0)', borderRadius: '8px', overflow: 'hidden' }}>
                <button
                  type="button"
                  onClick={() => setShowFlexibility((prev) => !prev)}
                  style={{
                    width: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '0.875rem 1rem',
                    background: 'var(--bg-secondary, #f8fafc)',
                    border: 'none',
                    cursor: 'pointer',
                    fontSize: '0.95rem',
                    fontWeight: '600',
                    color: 'var(--text-primary, #1e293b)'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <Sliders size={18} />
                    <span>Negotiation Flexibility (Optional)</span>
                  </div>
                  {showFlexibility ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                </button>

                {showFlexibility && (
                  <div style={{ padding: '1rem', background: 'var(--bg-card, #ffffff)', borderTop: '1px solid var(--border-color, #e2e8f0)' }}>
                    <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary, #64748b)', marginBottom: '1rem' }}>
                      Define what trade-offs the AI is authorized to negotiate autonomously. Any unconfigured dimension remains strictly fixed.
                    </p>

                    {/* Quantity Flexibility */}
                    <div style={{ marginBottom: '1.25rem', paddingBottom: '1rem', borderBottom: '1px dashed var(--border-color, #e2e8f0)' }}>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontWeight: '500', marginBottom: '0.5rem' }}>
                        <input
                          type="checkbox"
                          checked={allowQtyFlex}
                          onChange={(e) => setAllowQtyFlex(e.target.checked)}
                        />
                        <span>Allow AI to negotiate quantity</span>
                      </label>
                      {allowQtyFlex && (
                        <div className="form-row" style={{ marginTop: '0.5rem' }}>
                          <div className="form-group">
                            <label className="form-label" style={{ fontSize: '0.85rem' }}>Minimum Quantity</label>
                            <input
                              type="number"
                              className="input-field"
                              placeholder="e.g. 15"
                              value={qtyMin}
                              onChange={(e) => setQtyMin(e.target.value)}
                            />
                          </div>
                          <div className="form-group">
                            <label className="form-label" style={{ fontSize: '0.85rem' }}>Maximum Quantity</label>
                            <input
                              type="number"
                              className="input-field"
                              placeholder="e.g. 30"
                              value={qtyMax}
                              onChange={(e) => setQtyMax(e.target.value)}
                            />
                          </div>
                        </div>
                      )}
                    </div>

                    {/* Delivery Flexibility */}
                    <div style={{ marginBottom: '1.25rem', paddingBottom: '1rem', borderBottom: '1px dashed var(--border-color, #e2e8f0)' }}>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontWeight: '500', marginBottom: '0.5rem' }}>
                        <input
                          type="checkbox"
                          checked={allowDeliveryFlex}
                          onChange={(e) => setAllowDeliveryFlex(e.target.checked)}
                        />
                        <span>Allow AI to negotiate delivery timing</span>
                      </label>
                      {allowDeliveryFlex && (
                        <div className="form-group" style={{ marginTop: '0.5rem', maxWidth: '300px' }}>
                          <label className="form-label" style={{ fontSize: '0.85rem' }}>Maximum Acceptable Delivery Days</label>
                          <input
                            type="number"
                            className="input-field"
                            placeholder="e.g. 5"
                            value={deliveryMaxDays}
                            onChange={(e) => setDeliveryMaxDays(e.target.value)}
                            min="1"
                          />
                        </div>
                      )}
                    </div>

                    {/* Specs Flexibility */}
                    <div>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontWeight: '500', marginBottom: '0.5rem' }}>
                        <input
                          type="checkbox"
                          checked={allowSpecFlex}
                          onChange={(e) => setAllowSpecFlex(e.target.checked)}
                        />
                        <span>Allow AI to discuss specification alternatives</span>
                      </label>
                      {allowSpecFlex && (
                        <div className="form-group" style={{ marginTop: '0.5rem' }}>
                          <label className="form-label" style={{ fontSize: '0.85rem' }}>Explicitly Allowed Alternatives</label>
                          <input
                            type="text"
                            className="input-field"
                            placeholder="e.g. Equivalent brands: Schneider or ABB acceptable; Grade 304 or 316"
                            value={allowedSpecs}
                            onChange={(e) => setAllowedSpecs(e.target.value)}
                          />
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>

              <div className="form-actions">
                <button type="submit" className="btn btn-primary btn-lg" disabled={loading}>
                  <Send size={18} />
                  <span>{loading ? 'Matching Suppliers & Queuing...' : 'Submit & Match Suppliers'}</span>
                </button>
              </div>
            </form>
          ) : (
            <div>
              {errorMsg && <div className="error-alert">{errorMsg}</div>}
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
                          <th>Preferred Target Price</th>
                          <th>Maximum Acceptable Price</th>
                          <th>Category</th>
                          <th>Deadline (Hours)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {bulkRows.map((row) => (
                          <tr key={row.id}>
                            <td><input type="checkbox" checked={row.selected} onChange={() => toggleBulkRow(row.id)} /></td>
                            <td>{row.rowNumber}</td>
                            <td>
                              <input type="text" className="input-field" value={row.product_name} onChange={(e) => updateBulkRowField(row.id, 'product_name', e.target.value)} />
                            </td>
                            <td>
                              <input type="text" className="input-field" value={row.specs || ''} onChange={(e) => updateBulkRowField(row.id, 'specs', e.target.value)} />
                            </td>
                            <td>
                              <input type="number" className="input-field" value={row.quantity ?? ''} onChange={(e) => updateBulkRowField(row.id, 'quantity', e.target.value)} />
                            </td>
                            <td>{row.last_quote ?? '—'}</td>
                            <td>
                              <input
                                type="number"
                                className="input-field"
                                min="0.01"
                                step="0.01"
                                placeholder="e.g. 2.50"
                                value={row.acceptable_price_min ?? ''}
                                onChange={(e) => updateBulkRowField(row.id, 'acceptable_price_min', e.target.value)}
                              />
                            </td>
                            <td>
                              <input
                                type="number"
                                className="input-field"
                                min="0.01"
                                step="0.01"
                                placeholder="e.g. 4.00"
                                value={row.acceptable_price_max ?? ''}
                                onChange={(e) => updateBulkRowField(row.id, 'acceptable_price_max', e.target.value)}
                              />
                            </td>
                            <td>
                              <select value={row.category} onChange={(e) => updateBulkRowCategory(row.id, e.target.value)}>
                                {categories.map((cat) => (
                                  <option key={cat} value={cat}>{cat}</option>
                                ))}
                              </select>
                            </td>
                            <td>
                              <input type="number" className="input-field" min="1" value={row.deadline_hours ?? deadlineHours} onChange={(e) => updateBulkRowField(row.id, 'deadline_hours', e.target.value)} />
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
                      Contacted <strong>{matchedResult.matched_suppliers_count} supplier(s)</strong> via {matchedResult.targeting_mode === 'selected' ? 'your selected supplier list' : `categories: ${(matchedResult.categories || selectedCategories).join(', ')}`}. Outbound WhatsApp messages have been pushed to the pacing queue.
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
                  <p className="banner-subtitle">{matchedResult.message || 'No active suppliers matched this RFQ targeting selection.'}</p>
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
