import React, { useState, useEffect } from 'react';
import { supabase } from '../supabaseClient';
import AuthShell from '../components/AuthShell';

export default function ResetPassword({ navigate }) {
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const { data: sub } = supabase.auth.onAuthStateChange(() => {});
    return () => sub?.subscription?.unsubscribe?.();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMessage(null);
    try {
      const { error } = await supabase.auth.updateUser({ password });
      if (error) setMessage({ type: 'error', text: error.message });
      else {
        setMessage({ type: 'success', text: 'Password updated. You can now log in.' });
        navigate('/login');
      }
    } catch (err) {
      setMessage({ type: 'error', text: err.message || String(err) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell
      title="Choose a new password"
      subtitle="Enter a new password for your Amafha account."
      footer={
        <button type="button" className="btn-ghost auth-text-link" onClick={() => navigate('/login')}>
          Back to sign in
        </button>
      }
    >
      {message && <div className={`msg ${message.type}`}>{message.text}</div>}
      <form onSubmit={handleSubmit} className="auth-form">
        <div className="form-group">
          <label className="form-label" htmlFor="reset-password">New password</label>
          <input
            id="reset-password"
            className="input-field input-plain"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            autoComplete="new-password"
            required
          />
        </div>
        <button type="submit" className="btn btn-primary btn-lg auth-submit" disabled={loading}>
          {loading ? 'Updating...' : 'Set new password'}
        </button>
      </form>
    </AuthShell>
  );
}
