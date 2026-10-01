import React, { useState, useEffect } from 'react';
import { Users, Plus, Search, Filter, Edit2, Phone, Tag, Check, X, Trash2, ChevronDown } from 'lucide-react';
import { createSupplier, updateSupplier, deleteSupplier, fetchCategories, createCustomCategory } from '../api';

const DEFAULT_CATEGORIES = [
  'Electronics',
  'Hardware',
  'Plumbing',
  'Electrical',
  'Tools',
  'Building Materials',
  'General',
];

export default function SuppliersView({ suppliers, loading, refreshSuppliers, isAdmin = false }) {
  const [categories, setCategories] = useState(DEFAULT_CATEGORIES);
  const [searchTerm, setSearchTerm] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState(null);
  const [saving, setSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  // Custom Category State
  const [showCustomCatInput, setShowCustomCatInput] = useState(false);
  const [customCatName, setCustomCatName] = useState('');
  const [creatingCat, setCreatingCat] = useState(false);
  const [categoryDropdownOpen, setCategoryDropdownOpen] = useState(false);
  const [deletingSupplierId, setDeletingSupplierId] = useState(null);

  // Form State
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [selectedCategories, setSelectedCategories] = useState([]);
  const [notes, setNotes] = useState('');
  const [isActive, setIsActive] = useState(true);

  const loadCategories = async () => {
    try {
      const list = await fetchCategories();
      if (list && list.length) {
        setCategories(list);
      }
    } catch (e) {
      console.error('Error loading categories:', e);
    }
  };

  useEffect(() => {
    loadCategories();
  }, []);

  const openAddModal = () => {
    loadCategories();
    setEditingSupplier(null);
    setName('');
    setPhone('');
    setSelectedCategories(['Hardware']);
    setNotes('');
    setIsActive(true);
    setErrorMsg('');
    setShowCustomCatInput(false);
    setCustomCatName('');
    setCategoryDropdownOpen(false);
    setShowModal(true);
  };

  const openEditModal = (supplier) => {
    loadCategories();
    setEditingSupplier(supplier);
    setName(supplier.name || '');
    setPhone(supplier.phone_number || '');
    setSelectedCategories(supplier.category || []);
    setNotes(supplier.notes || '');
    setIsActive(supplier.is_active !== false);
    setErrorMsg('');
    setShowCustomCatInput(false);
    setCustomCatName('');
    setCategoryDropdownOpen(false);
    setShowModal(true);
  };

  const handleCreateCustomCategory = async (e) => {
    e.preventDefault();
    const clean = customCatName.trim();
    if (!clean) return;

    setCreatingCat(true);
    try {
      const created = await createCustomCategory(clean);
      if (!categories.includes(created)) {
        setCategories([...categories, created]);
      }
      if (!selectedCategories.includes(created)) {
        setSelectedCategories([...selectedCategories, created]);
      }
      setCustomCatName('');
      setShowCustomCatInput(false);
    } catch (err) {
      console.error(err);
      setErrorMsg(err.message || 'Failed to create custom category');
    } finally {
      setCreatingCat(false);
    }
  };

  const toggleCategory = (cat) => {
    if (selectedCategories.includes(cat)) {
      setSelectedCategories(selectedCategories.filter((c) => c !== cat));
    } else {
      setSelectedCategories([...selectedCategories, cat]);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim() || !phone.trim()) {
      setErrorMsg('Supplier name and phone number are required.');
      return;
    }
    if (selectedCategories.length === 0) {
      setErrorMsg('Please select at least one category.');
      return;
    }

    setSaving(true);
    setErrorMsg('');

    try {
      if (editingSupplier) {
        await updateSupplier(editingSupplier.id, {
          name,
          phone_number: phone,
          category: selectedCategories,
          notes,
          is_active: isActive,
        });
      } else {
        await createSupplier({
          name,
          phone_number: phone,
          category: selectedCategories,
          notes,
          is_active: isActive,
        });
      }
      setShowModal(false);
      await refreshSuppliers();
    } catch (err) {
      console.error(err);
      setErrorMsg(err.message || 'Error saving supplier');
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteSupplier = async (supplier) => {
    if (!isAdmin || !supplier?.id) return;
    const confirmed = window.confirm(
      `Delete ${supplier.name}? This removes the supplier from the active directory and future RFQ matching, while preserving historical procurement records.`
    );
    if (!confirmed) return;

    setDeletingSupplierId(supplier.id);
    setErrorMsg('');
    try {
      await deleteSupplier(supplier.id);
      if (editingSupplier?.id === supplier.id) {
        setShowModal(false);
        setEditingSupplier(null);
      }
      await refreshSuppliers();
    } catch (err) {
      console.error(err);
      setErrorMsg(err.message || 'Failed to delete supplier');
    } finally {
      setDeletingSupplierId(null);
    }
  };

  const filteredSuppliers = (suppliers || []).filter((s) => {
    const matchesSearch =
      s.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      s.phone_number.includes(searchTerm);

    const matchesCategory =
      !categoryFilter || (s.category && s.category.includes(categoryFilter));

    return matchesSearch && matchesCategory;
  });

  return (
    <div className="view-container">
      <div className="view-header">
        <div>
          <h2 className="view-title">Supplier Directory</h2>
          <p className="view-description">
            Manage suppliers and their specialized product categories. RFQs automatically match against these categories.
          </p>
        </div>
        <button className="btn btn-primary" onClick={openAddModal}>
          <Plus size={18} />
          <span>Add New Supplier</span>
        </button>
      </div>

      {/* Filter Bar */}
      <div className="filter-bar">
        <div className="search-input-wrap">
          <Search size={18} className="search-icon" />
          <input
            type="text"
            className="input-field search-input"
            placeholder="Search by supplier name or phone..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>

        <div className="filter-select-wrap">
          <Filter size={18} className="filter-icon" />
          <select
            className="input-field select-input"
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
          >
            <option value="">All Categories</option>
            {categories.map((cat) => (
              <option key={cat} value={cat}>
                {cat}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Suppliers Table */}
      <div className="card table-card">
        {loading ? (
          <div className="loading-state">Loading suppliers data...</div>
        ) : filteredSuppliers.length === 0 ? (
          <div className="empty-state">
            <Users size={40} className="empty-icon" />
            <h3>No Suppliers Found</h3>
            <p>Add suppliers to your directory so the AI agent can route RFQs to them.</p>
            <button className="btn btn-secondary btn-sm" onClick={openAddModal}>
              <Plus size={16} /> Add First Supplier
            </button>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Supplier Name</th>
                <th>Phone Number</th>
                <th>Categories</th>
                <th>Status</th>
                <th>Notes</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredSuppliers.map((supplier) => (
                <tr key={supplier.id}>
                  <td>
                    <div className="supplier-name-cell">
                      <strong>{supplier.name}</strong>
                    </div>
                  </td>
                  <td>
                    <span className="phone-tag">
                      <Phone size={14} />
                      {supplier.phone_number}
                    </span>
                  </td>
                  <td>
                    <div className="category-tag-group">
                      {(supplier.category || []).map((cat) => (
                        <span key={cat} className="badge badge-category">
                          {cat}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>
                    <span
                      className={`status-pill ${
                        supplier.is_active !== false ? 'status-active' : 'status-inactive'
                      }`}
                    >
                      {supplier.is_active !== false ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td>
                    <span className="notes-text">{supplier.notes || '-'}</span>
                  </td>
                  <td className="text-right">
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.35rem' }}>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => openEditModal(supplier)}
                      >
                        <Edit2 size={16} /> Edit
                      </button>
                      {isAdmin && (
                        <button
                          className="btn btn-danger btn-sm"
                          onClick={() => handleDeleteSupplier(supplier)}
                          disabled={deletingSupplierId === supplier.id}
                          title="Delete supplier"
                        >
                          <Trash2 size={16} />
                          {deletingSupplierId === supplier.id ? 'Deleting...' : 'Delete'}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Modal Dialog */}
      {showModal && (
        <div className="modal-backdrop">
          <div className="modal-card">
            <div className="modal-header">
              <h3 className="modal-title">
                {editingSupplier ? 'Edit Supplier' : 'Add New Supplier'}
              </h3>
              <button className="btn-icon" onClick={() => setShowModal(false)}>
                <X size={20} />
              </button>
            </div>

            <form onSubmit={handleSubmit}>
              <div className="modal-body">
                {errorMsg && <div className="error-alert">{errorMsg}</div>}

                <div className="form-group">
                  <label className="form-label">Supplier Name *</label>
                  <input
                    type="text"
                    className="input-field"
                    placeholder="e.g. Al Noor Hardware & Tools"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">WhatsApp Phone Number *</label>
                  <input
                    type="text"
                    className="input-field"
                    placeholder="e.g. +971501234567"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    required
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">
                    Categories * (Multi-select categories served)
                  </label>

                  <div style={{ position: 'relative' }}>
                    <button
                      type="button"
                      className="input-field"
                      onClick={() => setCategoryDropdownOpen((open) => !open)}
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
                      <div className="category-tag-group" style={{ marginTop: '0.5rem' }}>
                        {selectedCategories.map((cat) => (
                          <span key={cat} className="badge badge-category">
                            {cat}
                          </span>
                        ))}
                      </div>
                    )}

                    {categoryDropdownOpen && (
                      <div
                        style={{
                          position: 'absolute',
                          zIndex: 20,
                          top: '100%',
                          left: 0,
                          right: 0,
                          marginTop: '0.35rem',
                          maxHeight: '240px',
                          overflowY: 'auto',
                          background: 'var(--card-bg, #fff)',
                          border: '1px solid var(--border-color, #d1d5db)',
                          borderRadius: '8px',
                          boxShadow: '0 10px 30px rgba(0,0,0,0.16)',
                          padding: '0.4rem'
                        }}
                      >
                        {categories.map((cat) => {
                          const isSelected = selectedCategories.includes(cat);
                          return (
                            <button
                              type="button"
                              key={cat}
                              onClick={() => toggleCategory(cat)}
                              style={{
                                width: '100%',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '0.5rem',
                                padding: '0.55rem 0.6rem',
                                border: 0,
                                borderRadius: '6px',
                                background: isSelected ? 'rgba(99, 102, 241, 0.12)' : 'transparent',
                                cursor: 'pointer',
                                textAlign: 'left'
                              }}
                            >
                              <span style={{ width: 18 }}>{isSelected ? <Check size={15} /> : null}</span>
                              <Tag size={14} />
                              <span>{cat}</span>
                            </button>
                          );
                        })}

                        <div style={{ borderTop: '1px solid var(--border-color, #e5e7eb)', marginTop: '0.35rem', paddingTop: '0.4rem' }}>
                          {showCustomCatInput ? (
                            <div style={{ display: 'flex', gap: '0.4rem' }}>
                              <input
                                type="text"
                                className="input-field"
                                placeholder="Custom category..."
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
                              <button
                                type="button"
                                className="btn btn-primary btn-sm"
                                onClick={handleCreateCustomCategory}
                                disabled={creatingCat}
                              >
                                {creatingCat ? 'Adding...' : 'Add'}
                              </button>
                            </div>
                          ) : (
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              onClick={() => setShowCustomCatInput(true)}
                              style={{ width: '100%', justifyContent: 'flex-start' }}
                            >
                              <Plus size={14} /> Add Custom Category
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                <div className="form-group">
                  <label className="form-label">Notes (Optional)</label>
                  <textarea
                    className="input-field textarea-input"
                    placeholder="e.g. Reliable delivery, payment 30 days"
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    rows={3}
                  />
                </div>
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={saving}
                >
                  {saving ? 'Saving...' : editingSupplier ? 'Update Supplier' : 'Create Supplier'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
