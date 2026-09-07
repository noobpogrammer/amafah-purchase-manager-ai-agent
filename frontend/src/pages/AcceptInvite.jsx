import React, { useState, useEffect } from 'react';
import { supabase } from '../supabaseClient';
import AuthShell from '../components/AuthShell';
import { ensureProfile } from '../lib/ensureProfile';

export default function AcceptInvite({ navigate }) {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [userEmail, setUserEmail] = useState('');

  useEffect(() => {
    (async () => {
      const { data: userRes } = await supabase.auth.getUser();
      if (userRes?.user?.email) {
        setUserEmail(userRes.user.email);
      }
    })();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (password !== confirm) {
      setMessage({ type: 'error', text: 'Passwords do not match' });
      return;
    }
    if (password.length < 6) {
      setMessage({ type: 'error', text: 'Password must be at least 6 characters' });
      return;
    }

    setLoading(true);
    setMessage(null);
    try {
      const { error } = await supabase.auth.updateUser({ password });
      if (error) throw error;

      await ensureProfile();
      setMessage({ type: 'success', text: 'Account activated! Redirecting to workspace...' });
      setTimeout(() => {
        navigate('/');
      }, 1000);
    } catch (err) {
      setMessage({ type: 'error', text: err.message || String(err) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell
      title="Welcome to the Team"
      subtitle={userEmail ? `Set a password to complete your invite for ${userEmail}` : 'Set a password for your account to accept the invite.'}
      footer={
        <button type="button" className="btn-ghost auth-text-link" onClick={() => navigate('/login')}>
          Already set up? Sign in
        </button>
      }
    >
      {message && <div className={`msg ${message.type}`}>{message.text}</div>}
      <form onSubmit={handleSubmit} className="auth-form">
        <div className="form-group">
          <label className="form-label" htmlFor="invite-password">Create Password</label>
          <input
            id="invite-password"
            className="input-field input-plain"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            autoComplete="new-password"
            required
            placeholder="Min. 6 characters"
          />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="invite-confirm">Confirm Password</label>
          <input
            id="invite-confirm"
            className="input-field input-plain"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            type="password"
            autoComplete="new-password"
            required
            placeholder="Re-enter password"
          />
        </div>
        <button type="submit" className="btn btn-primary btn-lg auth-submit" disabled={loading}>
          {loading ? 'Activating account...' : 'Join Workspace'}
        </button>
      </form>
    </AuthShell>
  );
}
