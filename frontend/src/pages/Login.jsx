import React, { useState } from 'react';
import { supabase } from '../supabaseClient';
import { ensureProfile } from '../lib/ensureProfile';
import AuthShell from '../components/AuthShell';

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
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) {
        setMessage({ type: 'error', text: error.message });
        return;
      }

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
    <AuthShell
      title="Welcome back"
      subtitle="Sign in to open the Procurement Command Center."
      footer={
        <>
          Need an account?{' '}
          <button type="button" className="btn-ghost auth-text-link" onClick={() => navigate('/signup')}>
            Sign up
          </button>
        </>
      }
    >
      {message && <div className={`msg ${message.type}`}>{message.text}</div>}
      <form onSubmit={handleSubmit} className="auth-form">
        <div className="form-group">
          <label className="form-label" htmlFor="login-email">Email</label>
          <input
            id="login-email"
            className="input-field input-plain"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            type="email"
            autoComplete="email"
            required
          />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="login-password">Password</label>
          <input
            id="login-password"
            className="input-field input-plain"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            autoComplete="current-password"
            required
          />
        </div>
        <div className="auth-actions">
          <button type="submit" className="btn btn-primary btn-lg auth-submit" disabled={loading}>
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => navigate('/forgot-password')}>
            Forgot password?
          </button>
        </div>
      </form>
    </AuthShell>
  );
}
