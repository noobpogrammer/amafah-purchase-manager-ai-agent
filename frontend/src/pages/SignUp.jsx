import React, { useState, useEffect } from 'react';
import { supabase } from '../supabaseClient';

export default function SignUp({ navigate }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // Capture invite token from URL and persist across email verification flow
    const params = new URLSearchParams(window.location.search);
    const invite = params.get('invite');
    if (invite) {
      try {
        sessionStorage.setItem('pending_invite_token', invite);
      } catch (e) {
        // ignore
      }
    }
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setMessage(null);
    if (password !== confirm) {
      setMessage({ type: 'error', text: 'Passwords do not match' });
      return;
    }
    setLoading(true);
    try {
      const { data, error } = await supabase.auth.signUp({ email, password });
      if (error) {
        setMessage({ type: 'error', text: error.message });
      } else {
        setMessage({ type: 'success', text: 'Check your email to verify your account.' });
      }
    } catch (err) {
      setMessage({ type: 'error', text: err.message || String(err) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <h2>Sign up</h2>
      {message && <div className={`msg ${message.type}`}>{message.text}</div>}
      <form onSubmit={handleSubmit} className="auth-form">
        <label>Email</label>
        <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" required />
        <label>Password</label>
        <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" required />
        <label>Confirm password</label>
        <input value={confirm} onChange={(e) => setConfirm(e.target.value)} type="password" required />
        <button type="submit" disabled={loading}>{loading ? 'Signing up...' : 'Sign up'}</button>
      </form>
    </div>
  );
}
