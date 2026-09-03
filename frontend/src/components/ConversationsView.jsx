import React, { useState, useEffect } from 'react';
import { fetchMessages, fetchSuppliers, fetchRFQs, formatRfqDropdownLabel } from '../api';
import { MessageSquare, Phone, Filter, Clock, Bot, User } from 'lucide-react';

export default function ConversationsView() {
  const [messages, setMessages] = useState([]);
  const [suppliers, setSuppliers] = useState([]);
  const [rfqs, setRfqs] = useState([]);
  const [loading, setLoading] = useState(true);

  const [selectedSupplierId, setSelectedSupplierId] = useState('');
  const [selectedRfqId, setSelectedRfqId] = useState('');

  useEffect(() => {
    async function loadFilterOptions() {
      try {
        const [suppData, rfqData] = await Promise.all([fetchSuppliers(), fetchRFQs()]);
        setSuppliers(suppData || []);
        const sortedRfqs = (rfqData || []).sort(
          (a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)
        );
        setRfqs(sortedRfqs);
      } catch (err) {
        console.error('Error fetching options:', err);
      }
    }
    loadFilterOptions();
  }, []);

  const loadTranscript = async () => {
    setLoading(true);
    try {
      const data = await fetchMessages(selectedSupplierId || null, selectedRfqId || null);
      setMessages(data);
    } catch (err) {
      console.error('Error loading transcript:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTranscript();
  }, [selectedSupplierId, selectedRfqId]);

  return (
    <div className="view-container">
      <div className="view-header">
        <div>
          <h2 className="view-title">WhatsApp Audit Logs & Conversation Transcripts</h2>
          <p className="view-description">
            Complete back-and-forth transcript history of all WhatsApp messages exchanged between suppliers and the AI agent.
          </p>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="filter-bar">
        <div className="filter-select-wrap">
          <Filter size={18} className="filter-icon" />
          <select
            className="input-field select-input"
            value={selectedSupplierId}
            onChange={(e) => setSelectedSupplierId(e.target.value)}
          >
            <option value="">All Suppliers</option>
            {suppliers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.phone_number})
              </option>
            ))}
          </select>
        </div>

        <div className="filter-select-wrap">
          <Filter size={18} className="filter-icon" />
          <select
            className="input-field select-input"
            value={selectedRfqId}
            onChange={(e) => setSelectedRfqId(e.target.value)}
          >
            <option value="">All RFQs</option>
            {rfqs.map((r) => (
              <option key={r.id} value={r.id}>
                {formatRfqDropdownLabel(r)}
              </option>
            ))}
          </select>
        </div>

        <button className="btn btn-secondary btn-sm" onClick={loadTranscript}>
          Refresh Log
        </button>
      </div>

      {/* Transcript Feed Container */}
      <div className="card chat-transcript-card">
        {loading ? (
          <div className="loading-state">Loading WhatsApp transcript history...</div>
        ) : messages.length === 0 ? (
          <div className="empty-state">
            <MessageSquare size={40} className="empty-icon" />
            <h3>No WhatsApp Messages Logged</h3>
            <p>Messages exchanged with suppliers will appear here chronologically.</p>
          </div>
        ) : (
          <div className="chat-messages-container">
            {messages.map((msg) => {
              const isInbound = msg.direction === 'inbound';
              const supplierName = msg.suppliers?.name || 'Supplier';
              const phone = msg.suppliers?.phone_number || '';
              const rfqProduct = msg.rfqs?.product_name || '';

              return (
                <div
                  key={msg.id}
                  className={`message-bubble-wrapper ${isInbound ? 'inbound' : 'outbound'}`}
                >
                  <div className="avatar-wrap">
                    {isInbound ? <User size={16} /> : <Bot size={16} />}
                  </div>

                  <div className="message-bubble">
                    <div className="message-sender-meta">
                      <strong>{isInbound ? supplierName : 'Amafha AI Agent'}</strong>
                      {phone && <span className="phone-sub"><Phone size={11} /> {phone}</span>}
                      {rfqProduct && <span className="badge badge-category">{rfqProduct}</span>}
                    </div>

                    <div className="message-text-body">{msg.body}</div>

                    <div className="message-time">
                      <Clock size={11} />
                      {new Date(msg.created_at).toLocaleString([], {
                        dateStyle: 'short',
                        timeStyle: 'short',
                      })}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
