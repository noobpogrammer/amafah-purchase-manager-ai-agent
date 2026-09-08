import React, { useState } from 'react';
import { FileText, Download, Calendar, AlertCircle, CheckCircle2, Loader2, Info } from 'lucide-react';
import { downloadDailyReport } from '../api';

export default function DailyReportView() {
  const todayStr = new Date().toISOString().split('T')[0];
  const [selectedDate, setSelectedDate] = useState(todayStr);
  const [loading, setLoading] = useState(false);
  const [infoMessage, setInfoMessage] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);

  const handleGenerateReport = async (e) => {
    e.preventDefault();
    if (!selectedDate) {
      setErrorMessage('Please select a valid date.');
      return;
    }

    setLoading(true);
    setInfoMessage(null);
    setErrorMessage(null);
    setSuccessMessage(null);

    try {
      const result = await downloadDailyReport(selectedDate);
      if (result.type === 'json' && result.data?.status === 'no_data') {
        setInfoMessage('No RFQs were created on this date — no document can be generated.');
      } else if (result.type === 'blob') {
        const url = window.URL.createObjectURL(result.blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = result.filename || `Daily_Procurement_Report_${selectedDate}.docx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        setSuccessMessage(`Daily Procurement Report for ${selectedDate} has been generated and downloaded.`);
      }
    } catch (err) {
      console.error('Error generating daily report:', err);
      setErrorMessage(err.message || 'Failed to generate report.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="view-container">
      <div className="view-header">
        <div>
          <h2 className="view-title">Daily Procurement Report</h2>
          <p className="view-description">
            Export a comprehensive Word (.docx) report summarizing all RFQ activity, supplier quotes, and AI evaluations for any date.
          </p>
        </div>
      </div>

      <div className="max-w-2xl">
        <div className="card" style={{ maxWidth: '640px' }}>
          <div className="card-header">
            <h4 className="card-title flex-items">
              <FileText size={20} />
              <span>Export Daily Report (.docx)</span>
            </h4>
          </div>

          <form onSubmit={handleGenerateReport} className="report-form" style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
            <div>
              <label className="form-label" htmlFor="report-date-input" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
                <Calendar size={16} />
                <span>Select Report Date</span>
              </label>
              <input
                id="report-date-input"
                type="date"
                className="input-field"
                value={selectedDate}
                onChange={(e) => {
                  setSelectedDate(e.target.value);
                  setInfoMessage(null);
                  setErrorMessage(null);
                  setSuccessMessage(null);
                }}
                max={todayStr}
                disabled={loading}
                style={{ width: '100%', padding: '0.65rem 0.85rem' }}
                required
              />
            </div>

            {infoMessage && (
              <div className="card alert-banner info-banner" style={{ marginTop: '0.25rem' }}>
                <Info size={20} className="alert-icon" />
                <div>
                  <strong>Notice</strong>
                  <p>{infoMessage}</p>
                </div>
              </div>
            )}

            {errorMessage && (
              <div className="card alert-banner error-banner" style={{ marginTop: '0.25rem', borderColor: 'var(--color-danger, #ef4444)' }}>
                <AlertCircle size={20} className="alert-icon" style={{ color: 'var(--color-danger, #ef4444)' }} />
                <div>
                  <strong>Error</strong>
                  <p>{errorMessage}</p>
                </div>
              </div>
            )}

            {successMessage && (
              <div className="card alert-banner success-banner" style={{ marginTop: '0.25rem' }}>
                <CheckCircle2 size={20} className="alert-icon" style={{ color: 'var(--color-success, #10b981)' }} />
                <div>
                  <strong>Success</strong>
                  <p>{successMessage}</p>
                </div>
              </div>
            )}

            <div>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={loading || !selectedDate}
                style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', minWidth: '220px', justifyContent: 'center' }}
              >
                {loading ? (
                  <>
                    <Loader2 size={18} className="animate-spin" />
                    <span>Generating Report...</span>
                  </>
                ) : (
                  <>
                    <Download size={18} />
                    <span>Download Word Document</span>
                  </>
                )}
              </button>
            </div>
          </form>
        </div>

        <div className="card" style={{ maxWidth: '640px', marginTop: '1.5rem' }}>
          <div className="card-header">
            <h4 className="card-title flex-items">
              <Info size={18} />
              <span>About Daily Procurement Reports</span>
            </h4>
          </div>
          <div style={{ fontSize: '0.9rem', color: 'var(--text-secondary, #94a3b8)', lineHeight: '1.6' }}>
            <p style={{ marginBottom: '0.75rem' }}>
              The generated Microsoft Word (.docx) document includes:
            </p>
            <ul style={{ paddingLeft: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              <li><strong>Title & Date Header</strong> for documentation and auditing.</li>
              <li><strong>Per-RFQ breakdown</strong>: Product name, category, quantity, specs, and deadline.</li>
              <li><strong>Top 5 AI-Ranked Quotes</strong> evaluated by price, delivery speed, and warranty terms.</li>
              <li><strong>Unresponded RFQ notices</strong> ("No one responded to this RFQ.") when suppliers did not quote.</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
