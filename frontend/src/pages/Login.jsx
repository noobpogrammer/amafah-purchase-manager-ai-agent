import React, { useState } from 'react';
import { supabase } from '../supabaseClient';
import { ensureProfile } from '../lib/ensureProfile';

export default function Login({ navigate, onLoginSuccess }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setMessage(null);
    setLoading(true);
    try {
      const { data, error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) {
        setMessage({ type: 'error', text: error.message });
        return;
      }

      // Post-login bootstrap
      try {
        await ensureProfile();
      } catch (err) {
        setMessage({ type: 'error', text: err.message || 'Invite/claim error' });
        return;
      }

      setMessage({ type: 'success', text: 'Logged in' });
      if (onLoginSuccess) onLoginSuccess();
      navigate('/');
    } catch (err) {
      setMessage({ type: 'error', text: err.message || String(err) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <h2>Login</h2>
      {message && <div className={`msg ${message.type}`}>{message.text}</div>}
      <form onSubmit={handleSubmit} className="auth-form">
        <label>Email</label>
        <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" required />
        <label>Password</label>
        <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" required />
        <div className="auth-actions">
          <button type="submit" disabled={loading}>{loading ? 'Logging in...' : 'Login'}</button>
          <button type="button" className="link" onClick={() => navigate('/forgot-password')}>Forgot password?</button>
        </div>
      </form>
    </div>
  );
}
