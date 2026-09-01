import React, { useState } from 'react';
import { Users, Plus, Search, Filter, Edit2, Phone, Tag, Check, X } from 'lucide-react';
import { createSupplier, updateSupplier } from '../api';

const AVAILABLE_CATEGORIES = [
  'Electronics',
  'Hardware',
  'Plumbing',
  'Electrical',
  'Tools',
  'Building Materials',
  'General',
];

export default function SuppliersView({ suppliers, loading, refreshSuppliers }) {
  const [searchTerm, setSearchTerm] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState(null);
  const [saving, setSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  // Form State
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [selectedCategories, setSelectedCategories] = useState([]);
  const [notes, setNotes] = useState('');
  const [isActive, setIsActive] = useState(true);

  const openAddModal = () => {
    setEditingSupplier(null);
    setName('');
    setPhone('');
    setSelectedCategories(['Hardware']);
    setNotes('');
    setIsActive(true);
    setErrorMsg('');
    setShowModal(true);
  };

  const openEditModal = (supplier) => {
    setEditingSupplier(supplier);
    setName(supplier.name || '');
    setPhone(supplier.phone_number || '');
    setSelectedCategories(supplier.category || []);
    setNotes(supplier.notes || '');
    setIsActive(supplier.is_active !== false);
    setErrorMsg('');
    setShowModal(true);
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
            {AVAILABLE_CATEGORIES.map((cat) => (
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
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => openEditModal(supplier)}
                    >
                      <Edit2 size={16} /> Edit
                    </button>
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
                  <div className="category-selection-grid">
                    {AVAILABLE_CATEGORIES.map((cat) => {
                      const isSelected = selectedCategories.includes(cat);
                      return (
                        <button
                          type="button"
                          key={cat}
                          onClick={() => toggleCategory(cat)}
                          className={`category-toggle-chip ${
                            isSelected ? 'selected' : ''
                          }`}
                        >
                          <Tag size={14} />
                          <span>{cat}</span>
                          {isSelected && <Check size={14} />}
                        </button>
                      );
                    })}
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
