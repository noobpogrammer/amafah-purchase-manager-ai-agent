import React, { useState, useEffect } from 'react';
import useOutsideDismiss from '../hooks/useOutsideDismiss';
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
  const [supplierPickerOpen, setSupplierPickerOpen] = useState(false);
  const supplierPickerRef = useOutsideDismiss(supplierPickerOpen, () => setSupplierPickerOpen(false));
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
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false);
  const [categorySearch, setCategorySearch] = useState('');
  const categoryPickerRef = useOutsideDismiss(categoryPickerOpen, () => setCategoryPickerOpen(false));

  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [matchedResult, setMatchedResult] = useState(null);

  const [isBulkMode, setIsBulkMode] = useState(false);
  const [bulkFile, setBulkFile] = useState(null);
  const [bulkRows, setBulkRows] = useState([]);
  const [bulkSubmitting, setBulkSubmitting] = useState(false);
  const [bulkSummary, setBulkSummary] = useState(null);
  const [bulkCategoryOverride, setBulkCategoryOverride] = useState('');
  const [bulkDeadlineOverride, setBulkDeadlineOverride] = useState('');
  const [expandedBulkIds, setExpandedBulkIds] = useState([]);
  const [bulkSearch, setBulkSearch] = useState('');
  const [bulkPage, setBulkPage] = useState(0);

  const loadCategories = async () => {
    try {
      const list = await fetchCategories();
      const usable = list || [];
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
    if (!file) { setBulkFile(null); setBulkRows([]); setBulkSummary(null); return; }

    const ext = file.name.split('.').pop()?.toLowerCase();
    if (ext !== 'csv') {
      setBulkFile(null); setBulkRows([]); setBulkSummary(null);
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
          const descriptionRaw = getSheetValue(row, ['description', 'item description', 'product name', 'product']);
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
      setExpandedBulkIds([]);
      setBulkPage(0);
      setBulkSearch('');
      setBulkSummary(null);
      if (!mappedRows.length) {
        setErrorMsg('No usable rows were detected in the uploaded file.');
      }
    } catch (err) {
      console.error(err);
      setBulkFile(null); setBulkRows([]);
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
      if (!row.product_name.trim() || !row.category || !Number.isFinite(Number(row.deadline_hours)) || Number(row.deadline_hours) <= 0) {
        setErrorMsg(`Row ${row.rowNumber}: Product name, category and a positive deadline are required.`);
        return;
      }
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
    const selectedSheet = XLSX.utils.json_to_sheet(selectedRows.map(row => ({ 'SL #': row.rowNumber, Description: row.description || row.product_name, Qty: row.quantity ?? '', 'Last Cost': row.last_quote ?? '' })));
    const selectedCsv = XLSX.utils.sheet_to_csv(selectedSheet);
    formData.append('file', new File([selectedCsv], bulkFile.name, { type: 'text/csv' }));
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

  const filteredBulkRows = bulkRows.filter(row => `${row.product_name} ${row.category}`.toLowerCase().includes(bulkSearch.trim().toLowerCase()));
  const bulkPageCount = Math.max(1, Math.ceil(filteredBulkRows.length / 20));
  const currentBulkPage = Math.min(bulkPage, bulkPageCount - 1);
  const visibleBulkRows = filteredBulkRows.slice(currentBulkPage * 20, (currentBulkPage + 1) * 20);

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
            <form className="rfq-create-form" onSubmit={handleSubmit}>
              {errorMsg && <div className="error-alert">{errorMsg}</div>}

              <nav className="form-section-nav" aria-label="Request form sections">
                <a href="#request-item">1. Item details</a>
                <a href="#request-suppliers">2. Suppliers & timing</a>
                <a href="#request-price">3. Price & negotiation</a>
              </nav>
              <fieldset id="request-item" className="rfq-form-section">
                <legend><span className="form-section-number">1</span>Item details</legend>
                <p className="form-section-description">What are you buying?</p>
              <div className="form-group">
                <label className="form-label flex-items" htmlFor="rfq-productName">
                  <Package size={16} /> Product Name *
                </label>
                <input
                  id="rfq-productName" type="text"
                  className="input-field"
                  placeholder="e.g. Copper Water Pipe 1/2 Inch"
                  value={productName}
                  onChange={(e) => setProductName(e.target.value)}
                  required
                />
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label className="form-label flex-items" htmlFor="rfq-specs">
                    <FileText size={16} /> Specifications / Notes *
                  </label>
                  <input
                    id="rfq-specs" type="text"
                    className="input-field"
                    placeholder="e.g. Type L, ASTM B88 compliant, 20ft lengths"
                    value={specs}
                    onChange={(e) => setSpecs(e.target.value)}
                    required
                  />
                </div>

                <div className="form-group">
                  <label className="form-label flex-items" htmlFor="rfq-quantity">Quantity (Units)</label>
                  <input
                    id="rfq-quantity" type="number"
                    className="input-field"
                    placeholder="e.g. 50"
                    value={quantity}
                    onChange={(e) => setQuantity(e.target.value)}
                  />
                </div>
              </div>

              </fieldset>

              <fieldset id="request-suppliers" className="rfq-form-section">
                <legend><span className="form-section-number">2</span>Suppliers & timing</legend>
                <p className="form-section-description">Choose who receives this request and when you need a reply.</p>
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label flex-items">
                    <Tag size={16} /> Supplier Categories *
                  </label>
                  <div ref={categoryPickerRef} style={{ position: 'relative' }}>
                    <button
                      type="button"
                      className="input-field"
                      aria-expanded={categoryPickerOpen}
                      aria-label="Choose supplier categories"
                      onClick={() => setCategoryPickerOpen((open) => !open)}
                      style={{
                        width: '100%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        cursor: 'pointer',
                        textAlign: 'left'
                      }}
                    >
                      <span>
                        {selectedCategories.length
                          ? `${selectedCategories.length} categor${selectedCategories.length === 1 ? 'y' : 'ies'} selected`
                          : 'Select categories'}
                      </span>
                      <ChevronDown size={16} />
                    </button>

                    {selectedCategories.length > 0 && (
                      <div className="category-tag-group" style={{ marginTop: '0.45rem' }}>
                        {selectedCategories.map((cat) => (
                          <span key={cat} className="badge badge-category">{cat}</span>
                        ))}
                      </div>
                    )}

                    {categoryPickerOpen && (
                      <div
                        style={{
                          position: 'absolute',
                          zIndex: 30,
                          top: '100%',
                          left: 0,
                          right: 0,
                          marginTop: '0.35rem',
                          background: 'var(--surface-subtle)',
                          color: 'var(--text-main)',
                          border: '1px solid var(--panel-border)',
                          borderRadius: '8px',
                          boxShadow: '0 12px 32px rgba(0,0,0,0.2)',
                          overflow: 'hidden'
                        }}
                      >
                        <div style={{ padding: '0.5rem', background: 'var(--surface-subtle)', borderBottom: '1px solid var(--panel-border)' }}>
                          <input
                            type="search"
                            className="input-field"
                            placeholder="Search categories..."
                            style={{ background: 'var(--surface-subtle)', color: 'var(--text-main)', paddingLeft: '1rem' }}
                            value={categorySearch}
                            onChange={(e) => setCategorySearch(e.target.value)}
                          />
                        </div>

                        <div style={{ maxHeight: '230px', overflowY: 'auto', padding: '0.35rem', background: 'var(--surface-subtle)', color: 'var(--text-main)' }}>
                          {categories
                            .filter((cat) => cat.toLowerCase().includes(categorySearch.trim().toLowerCase()))
                            .map((cat) => {
                              const checked = selectedCategories.includes(cat);
                              return (
                                <label
                                  key={cat}
                                  htmlFor={`rfq-category-${encodeURIComponent(cat)}`}
                                  className="checkbox-option"
                                  style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: '0.5rem',
                                    padding: '0.45rem 0.5rem',
                                    cursor: 'pointer',
                                    borderRadius: '6px',
                                    color: 'var(--text-main)'
                                  }}
                                >
                                  <input
                                    type="checkbox"
                                    id={`rfq-category-${encodeURIComponent(cat)}`}
                                    checked={checked}
                                    onChange={() => toggleRfqCategory(cat)}
                                  />
                                  <span>{cat}</span>
                                </label>
                              );
                            })}
                        </div>

                        <div style={{ padding: '0.5rem', background: 'var(--surface-subtle)', borderTop: '1px solid var(--panel-border)' }}>
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
                                style={{ flex: 1, minWidth: 0 }}
                              />
                              <button type="button" className="btn btn-primary btn-sm" onClick={handleCreateCustomCategory} disabled={creatingCat}>
                                {creatingCat ? 'Adding...' : 'Add'}
                              </button>
                              <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setShowCustomCatInput(false); setCustomCatName(''); }}>
                                Cancel
                              </button>
                            </div>
                          ) : (
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              onClick={() => setShowCustomCatInput(true)}
                              style={{ width: '100%', justifyContent: 'flex-start' }}
                            >
                              + Create Custom Category
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                  <span className="field-hint">
                    Select one or more categories. Matching uses ANY selected category.
                  </span>
                </div>

                <div className="form-group">
                  <label className="form-label">Send RFQ To</label>
                  <select
                    className="input-field"
                    value={targetingMode}
                    onChange={(e) => { setTargetingMode(e.target.value); setSupplierPickerOpen(e.target.value === 'selected'); }}
                  >
                    <option value="category">All suppliers matching selected categories</option>
                    <option value="selected">Specific supplier(s)</option>
                  </select>

                  {targetingMode === 'selected' && (
                    <div className="recipient-picker" ref={supplierPickerRef}>
                      <button type="button" className="btn btn-secondary recipient-trigger" aria-expanded={supplierPickerOpen} onClick={() => setSupplierPickerOpen(open => !open)}>
                        {selectedSupplierIds.length ? `Change suppliers (${selectedSupplierIds.length})` : 'Choose suppliers'} <ChevronDown size={16} />
                      </button>
                      <div className="category-tag-group selected-recipients">
                        {availableSuppliers.filter(s => selectedSupplierIds.includes(s.id)).map(s => <span key={s.id} className="badge badge-category">{s.name}<button type="button" className="chip-remove" aria-label={`Remove ${s.name}`} onClick={() => toggleTargetSupplier(s.id)}>×</button></span>)}
                      </div>
                      {!selectedSupplierIds.length && <span className="field-hint">No suppliers selected yet.</span>}
                      {supplierPickerOpen && <div className="recipient-panel" role="region" aria-label="Choose suppliers">
                        <input type="search" className="input-field" aria-label="Search suppliers" placeholder="Search suppliers..." value={supplierSearch} onChange={e => setSupplierSearch(e.target.value)} />
                        <div className="recipient-options">
                          {availableSuppliers.filter(s => !supplierSearch.trim() || `${s.name} ${s.phone_number}`.toLowerCase().includes(supplierSearch.trim().toLowerCase())).map(s => <label key={s.id} htmlFor={`recipient-${s.id}`} className="recipient-option"><input id={`recipient-${s.id}`} type="checkbox" checked={selectedSupplierIds.includes(s.id)} onChange={() => toggleTargetSupplier(s.id)} /><span><strong>{s.name}</strong><span className="phone-sub">{s.phone_number}</span></span></label>)}
                          {!availableSuppliers.length && <p className="field-hint">No active suppliers available.</p>}
                        </div>
                        <div className="recipient-panel-footer"><span>{selectedSupplierIds.length} selected</span><button type="button" className="btn btn-primary btn-sm" onClick={() => setSupplierPickerOpen(false)}>Done</button></div>
                      </div>}
                    </div>
                  )}
                </div>


              </div>

              <div className="form-row">
                <div className="form-group">
                  <label className="form-label flex-items" htmlFor="rfq-deadlineHours">
                    <Clock size={16} /> Response Deadline (Hours) *
                  </label>
                  <input
                    id="rfq-deadlineHours" type="number"
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
                <div className="form-group">
                  <label className="form-label flex-items" htmlFor="rfq-requiredDeliveryDays">
                    <Clock size={16} /> Required Delivery (Days)
                  </label>
                  <input
                    id="rfq-requiredDeliveryDays" type="number"
                    className="input-field"
                    placeholder="e.g. 2 (Leave empty if not specified)"
                    min="1"
                    value={requiredDeliveryDays}
                    onChange={(e) => setRequiredDeliveryDays(e.target.value)}
                  />
                  <span className="field-hint">Optional baseline delivery timeline.</span>
                </div>

              </div>
              </fieldset>

              <fieldset id="request-price" className="rfq-form-section">
                <legend><span className="form-section-number">3</span>Price & negotiation</legend>
                <p className="form-section-description">Optional. Set price limits and any flexibility the agent may use.</p>
              <div className="form-row price-fields">
                <div className="form-group">
                  <label className="form-label flex-items" htmlFor="rfq-lastQuote">Last Quote / Last Cost (AED)</label>
                  <input
                    id="rfq-lastQuote" type="number"
                    className="input-field"
                    placeholder="e.g. 12.50"
                    value={lastQuote}
                    onChange={(e) => setLastQuote(e.target.value)}
                    step="0.01"
                  />
                </div>

                <div className="form-group">
                  <label className="form-label flex-items" htmlFor="rfq-acceptablePriceMin">Acceptable Price Min (AED)</label>
                  <input
                    id="rfq-acceptablePriceMin" type="number"
                    className="input-field"
                    placeholder="e.g. 50.00"
                    value={acceptablePriceMin}
                    onChange={(e) => setAcceptablePriceMin(e.target.value)}
                    step="0.01"
                  />
                </div>

                <div className="form-group">
                  <label className="form-label flex-items" htmlFor="rfq-acceptablePriceMax">Acceptable Price Max (AED)</label>
                  <input
                    id="rfq-acceptablePriceMax" type="number"
                    className="input-field"
                    placeholder="e.g. 60.00"
                    value={acceptablePriceMax}
                    onChange={(e) => setAcceptablePriceMax(e.target.value)}
                    step="0.01"
                  />
                </div>
              </div>

              {/* Optional Collapsible Negotiation Flexibility Section */}
              <div style={{ marginTop: '1.25rem', marginBottom: '1.25rem', border: '1px solid var(--border-color, var(--panel-border))', borderRadius: '8px', overflow: 'hidden' }}>
                <button
                  type="button"
                  onClick={() => setShowFlexibility((prev) => !prev)}
                  style={{
                    width: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '0.875rem 1rem',
                    background: 'var(--bg-secondary, var(--surface-subtle))',
                    border: 'none',
                    cursor: 'pointer',
                    fontSize: '0.95rem',
                    fontWeight: '600',
                    color: 'var(--text-primary, var(--text-main))'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <Sliders size={18} />
                    <span>Negotiation Flexibility (Optional)</span>
                  </div>
                  {showFlexibility ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                </button>

                {showFlexibility && (
                  <div style={{ padding: '1rem', background: 'var(--bg-card, #ffffff)', borderTop: '1px solid var(--border-color, var(--panel-border))' }}>
                    <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary, var(--text-muted))', marginBottom: '1rem' }}>
                      Define what trade-offs the AI is authorized to negotiate autonomously. Any unconfigured dimension remains strictly fixed.
                    </p>

                    {/* Quantity Flexibility */}
                    <div style={{ marginBottom: '1.25rem', paddingBottom: '1rem', borderBottom: '1px dashed var(--border-color, var(--panel-border))' }}>
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
                    <div style={{ marginBottom: '1.25rem', paddingBottom: '1rem', borderBottom: '1px dashed var(--border-color, var(--panel-border))' }}>
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

              </fieldset>

              <div className="form-actions">
                <button type="submit" className="btn btn-primary btn-lg" disabled={loading}>
                  <Send size={18} />
                  <span>{loading ? 'Matching Suppliers & Queuing...' : 'Submit & Match Suppliers'}</span>
                </button>
              </div>
            </form>
          ) : (
            <div className="bulk-workspace">
              {errorMsg && <div className="error-alert" role="alert">{errorMsg}</div>}
              <section className="bulk-upload-zone">
                <h3>1. Upload your item list</h3>
                <p className="field-hint">Choose a CSV, then review the items before creating requests.</p>
                <label className="form-label" htmlFor="bulk-rfq-input">Material requisition (.csv)</label>
                <input id="bulk-rfq-input" type="file" accept=".csv" className="input-field" onChange={handleBulkFileChange} />
              </section>
              {bulkRows.length > 0 && <>
                <section className="bulk-defaults">
                  <div><h3>2. Set shared values</h3><p className="field-hint">Apply these to selected items. You can edit each item below.</p></div>
                  <div className="bulk-independent-defaults">
                    <div className="bulk-default-field"><label className="form-label" htmlFor="bulk-category">Default category</label><div className="bulk-default-action"><select id="bulk-category" className="input-field" value={bulkCategoryOverride} onChange={e => setBulkCategoryOverride(e.target.value)}><option value="">No change to categories</option>{categories.map(cat => <option key={cat} value={cat}>{cat}</option>)}</select><button type="button" className="btn btn-secondary" disabled={!bulkCategoryOverride || !bulkRows.some(r => r.selected)} onClick={() => setBulkRows(rows => rows.map(row => row.selected ? {...row, category:bulkCategoryOverride} : row))}>Apply category</button></div></div>
                    <div className="bulk-default-field"><label className="form-label" htmlFor="bulk-deadline">Default deadline (hours)</label><div className="bulk-default-action"><input id="bulk-deadline" type="number" min="1" placeholder="No change" className="input-field" value={bulkDeadlineOverride} onChange={e => setBulkDeadlineOverride(e.target.value)} /><button type="button" className="btn btn-secondary" disabled={!bulkRows.some(r => r.selected) || !Number.isFinite(Number(bulkDeadlineOverride)) || Number(bulkDeadlineOverride) <= 0} onClick={() => setBulkRows(rows => rows.map(row => row.selected ? {...row, deadline_hours:Number(bulkDeadlineOverride)} : row))}>Apply deadline</button></div></div>
                  </div>
                </section>
                <section className="bulk-review">
                  <div className="bulk-review-header"><div><h3>3. Review items</h3><p className="field-hint" role="status">{bulkRows.filter(r => r.selected).length} of {bulkRows.length} selected for upload</p></div><div className="flex-items gap-2"><button type="button" className="btn btn-secondary btn-sm" onClick={() => setExpandedBulkIds(expandedBulkIds.length === bulkRows.length ? [] : bulkRows.map(r => r.id))}>{expandedBulkIds.length === bulkRows.length ? 'Collapse all details' : 'Expand all details'}</button><button type="button" className="btn btn-secondary btn-sm" onClick={() => setBulkRows(rows => rows.map(r => ({...r, selected:true})))}>Select all</button><button type="button" className="btn btn-ghost btn-sm" onClick={() => setBulkRows(rows => rows.map(r => ({...r, selected:false})))}>Clear selection</button></div></div>
                  <div className="bulk-search"><label className="form-label" htmlFor="bulk-search">Find an item</label><input id="bulk-search" type="search" className="input-field" placeholder="Search product or category..." value={bulkSearch} onChange={e => {setBulkSearch(e.target.value);setBulkPage(0);}} /><span className="field-hint">Select all and Clear selection apply to the full file.</span></div>
                  <div className="bulk-compact-list">
                    {visibleBulkRows.map(row => <article key={row.id} className={`bulk-item bulk-compact-item ${row.selected ? 'is-selected' : ''}`} aria-label={`CSV row ${row.rowNumber}`}>
                      <div className="bulk-row-main">
                        <div className="bulk-row-product"><label className="bulk-item-selection" htmlFor={`bulk-select-${row.id}`}><input id={`bulk-select-${row.id}`} type="checkbox" checked={row.selected} onChange={() => toggleBulkRow(row.id)} />Row {row.rowNumber} · {row.selected ? 'Included' : 'Excluded'}</label><strong className="bulk-product-title">{row.product_name || 'Unnamed item'}</strong></div>
                        <div className="form-group"><label className="form-label" htmlFor={`bulk-cat-${row.id}`}>Category</label><select id={`bulk-cat-${row.id}`} className="input-field" value={row.category} onChange={e => updateBulkRowCategory(row.id,e.target.value)}>{categories.map(cat => <option key={cat} value={cat}>{cat}</option>)}</select></div>
                        <div className="form-group"><label className="form-label" htmlFor={`bulk-qty-${row.id}`}>Quantity</label><input id={`bulk-qty-${row.id}`} type="number" className="input-field" value={row.quantity ?? ''} onChange={e => updateBulkRowField(row.id, 'quantity', e.target.value)} /></div>
                        <div className="form-group"><label className="form-label" htmlFor={`bulk-hours-${row.id}`}>Reply within (hours)</label><input id={`bulk-hours-${row.id}`} type="number" min="1" className="input-field" value={row.deadline_hours ?? ''} onChange={e => updateBulkRowField(row.id,'deadline_hours',e.target.value)} /></div>
                        {[['acceptable_price_min','Target price (AED)'],['acceptable_price_max','Maximum price (AED)']].map(([field,label]) => <div className="form-group" key={field}><label className="form-label" htmlFor={`bulk-${field}-${row.id}`}>{label}</label><input id={`bulk-${field}-${row.id}`} type="number" min="0.01" step="0.01" className="input-field" value={row[field] ?? ''} onChange={e => updateBulkRowField(row.id,field,e.target.value)} /></div>)}
                        <button type="button" className="btn btn-secondary btn-sm bulk-detail-toggle" aria-expanded={expandedBulkIds.includes(row.id)} aria-controls={`bulk-detail-${row.id}`} onClick={() => setExpandedBulkIds(ids => ids.includes(row.id) ? ids.filter(id => id !== row.id) : [...ids,row.id])}>{expandedBulkIds.includes(row.id) ? 'Less' : 'Details'}<ChevronDown size={14} /></button>
                      </div>
                      <div id={`bulk-detail-${row.id}`} className="bulk-row-details" hidden={!expandedBulkIds.includes(row.id)}>
                        <div className="form-group"><label className="form-label" htmlFor={`bulk-name-${row.id}`}>Product name</label><textarea id={`bulk-name-${row.id}`} rows={2} className="input-field" value={row.product_name} onChange={e => updateBulkRowField(row.id, 'product_name', e.target.value)} /></div>
                        <div className="form-group"><label className="form-label" htmlFor={`bulk-cost-${row.id}`}>Previous cost (AED)</label><input id={`bulk-cost-${row.id}`} type="number" min="0.01" step="0.01" className="input-field" value={row.last_quote ?? ''} onChange={e => updateBulkRowField(row.id,'last_quote',e.target.value)} /></div>
                        <div className="form-group"><label className="form-label" htmlFor={`bulk-specs-${row.id}`}>Specifications</label><textarea id={`bulk-specs-${row.id}`} className="input-field" rows={2} value={row.specs || ''} onChange={e => updateBulkRowField(row.id,'specs',e.target.value)} /></div>
                        <p className="field-hint">Original: {row.description}</p>
                      </div>
                    </article>)}
                  </div>                  <div className="bulk-pagination"><span>{filteredBulkRows.length ? `${currentBulkPage * 20 + 1}–${Math.min((currentBulkPage + 1) * 20, filteredBulkRows.length)} of ${filteredBulkRows.length} items` : 'No matching items'}</span><div className="flex-items gap-2"><button type="button" className="btn btn-secondary btn-sm" disabled={currentBulkPage === 0} onClick={() => setBulkPage(currentBulkPage - 1)}>Previous items</button><span>Page {currentBulkPage + 1} of {bulkPageCount}</span><button type="button" className="btn btn-secondary btn-sm" disabled={currentBulkPage >= bulkPageCount - 1} onClick={() => setBulkPage(currentBulkPage + 1)}>Next items</button></div></div>

                </section>
                <div className="bulk-submit-bar"><span>Only selected items will be uploaded.</span><button type="button" className="btn btn-primary" onClick={submitBulkRows} disabled={bulkSubmitting || !bulkRows.some(r => r.selected)}>{bulkSubmitting ? 'Submitting RFQs...' : `Create ${bulkRows.filter(r => r.selected).length} request${bulkRows.filter(r => r.selected).length === 1 ? '' : 's'}`}</button></div>
              </>}
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
                  Created <strong>{bulkSummary.created_count}</strong> {bulkSummary.created_count === 1 ? 'RFQ' : 'RFQs'}. Review the summary below.
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
