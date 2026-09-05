import React, { useState } from 'react';
import { supabase } from '../supabaseClient';
import AuthShell from '../components/AuthShell';

export default function ForgotPassword({ navigate }) {
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMessage(null);
    try {
      const { error } = await supabase.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/reset-password`,
      });
      if (error) setMessage({ type: 'error', text: error.message });
      else setMessage({ type: 'success', text: 'Check your email for reset instructions.' });
    } catch (err) {
      setMessage({ type: 'error', text: err.message || String(err) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell
      title="Reset your password"
      subtitle="We’ll email a reset link if an account exists for that address."
      footer={
        <button type="button" className="btn-ghost auth-text-link" onClick={() => navigate('/login')}>
          Back to sign in
        </button>
      }
    >
      {message && <div className={`msg ${message.type}`}>{message.text}</div>}
      <form onSubmit={handleSubmit} className="auth-form">
        <div className="form-group">
          <label className="form-label" htmlFor="forgot-email">Email</label>
          <input
            id="forgot-email"
            className="input-field input-plain"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            type="email"
            autoComplete="email"
            required
          />
        </div>
        <button type="submit" className="btn btn-primary btn-lg auth-submit" disabled={loading}>
          {loading ? 'Sending...' : 'Send reset email'}
        </button>
      </form>
    </AuthShell>
  );
}
