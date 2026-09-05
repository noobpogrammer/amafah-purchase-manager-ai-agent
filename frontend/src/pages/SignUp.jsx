import React, { useState, useEffect } from 'react';
import { supabase } from '../supabaseClient';
import AuthShell from '../components/AuthShell';

export default function SignUp({ navigate }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [inviteToken, setInviteToken] = useState(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const invite = params.get('invite');
    if (invite) {
      setInviteToken(invite);
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
      const { error } = await supabase.auth.signUp({ email, password });
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
    <AuthShell
      title={inviteToken ? 'Join your team' : 'Create your account'}
      subtitle={
        inviteToken
          ? 'You were invited to Amafha. Create an account to accept the invite after email verification.'
          : 'Set up access to the Procurement Command Center.'
      }
      footer={
        <>
          Already have an account?{' '}
          <button type="button" className="btn-ghost auth-text-link" onClick={() => navigate('/login')}>
            Sign in
          </button>
        </>
      }
    >
      {inviteToken && (
        <div className="alert-banner info-banner auth-invite-banner">
          <div className="alert-content">
            <div>
              <strong>Team invitation detected</strong>
              <p>This signup is linked to a pending invite token.</p>
            </div>
          </div>
        </div>
      )}
      {message && <div className={`msg ${message.type}`}>{message.text}</div>}
      <form onSubmit={handleSubmit} className="auth-form">
        <div className="form-group">
          <label className="form-label" htmlFor="signup-email">Email</label>
          <input
            id="signup-email"
            className="input-field input-plain"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            type="email"
            autoComplete="email"
            required
          />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="signup-password">Password</label>
          <input
            id="signup-password"
            className="input-field input-plain"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            autoComplete="new-password"
            required
          />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="signup-confirm">Confirm password</label>
          <input
            id="signup-confirm"
            className="input-field input-plain"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            type="password"
            autoComplete="new-password"
            required
          />
        </div>
        <button type="submit" className="btn btn-primary btn-lg auth-submit" disabled={loading}>
          {loading ? 'Creating account...' : 'Sign up'}
        </button>
      </form>
    </AuthShell>
  );
}
