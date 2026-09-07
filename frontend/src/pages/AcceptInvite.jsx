import React, { useState, useEffect } from 'react';
import { supabase } from '../supabaseClient';
import { validateInviteToken, claimInviteToken } from '../api';
import AuthShell from '../components/AuthShell';

export default function AcceptInvite({ navigate }) {
  const [token, setToken] = useState(null);
  const [inviteInfo, setInviteInfo] = useState(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [message, setMessage] = useState(null);
  const [validating, setValidating] = useState(true);
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get('token') || sessionStorage.getItem('pending_invite_token');

    if (!urlToken) {
      setValidating(false);
      setMessage({
        type: 'error',
        text: 'No invitation token found in this link. Please ask your administrator to send a fresh invitation link.',
      });
      return;
    }

    setToken(urlToken);
    try {
      sessionStorage.setItem('pending_invite_token', urlToken);
    } catch (e) {
      // ignore
    }

    (async () => {
      setValidating(true);
      try {
        const info = await validateInviteToken(urlToken);
        setInviteInfo(info);
      } catch (err) {
        setMessage({
          type: 'error',
          text: err.message || 'This invitation link is invalid, expired, or has already been used.',
        });
      } finally {
        setValidating(false);
      }
    })();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setMessage(null);

    if (!inviteInfo || !inviteInfo.client_id) {
      setMessage({ type: 'error', text: 'Invalid invitation. Cannot create account.' });
      return;
    }

    if (password !== confirm) {
      setMessage({ type: 'error', text: 'Passwords do not match' });
      return;
    }

    if (password.length < 6) {
      setMessage({ type: 'error', text: 'Password must be at least 6 characters' });
      return;
    }

    setLoading(true);
    try {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          data: {
            client_id: inviteInfo.client_id,
            role: inviteInfo.role || 'member',
          },
          emailRedirectTo: `${window.location.origin}/login`,
        },
      });

      if (error) throw error;

      // Mark token claimed
      if (token) {
        try {
          await claimInviteToken(token);
          sessionStorage.removeItem('pending_invite_token');
        } catch (claimErr) {
          console.warn('Could not mark token as claimed:', claimErr);
        }
      }

      setSuccess(true);
      setMessage({
        type: 'success',
        text: 'Account created! Please check your email to confirm your account, then sign in to access your workspace.',
      });
    } catch (err) {
      setMessage({ type: 'error', text: err.message || String(err) });
    } finally {
      setLoading(false);
    }
  };

  if (validating) {
    return (
      <AuthShell
        title="Validating Invitation"
        subtitle="Checking your workspace invitation..."
      >
        <div className="loading-state">Validating invitation token...</div>
      </AuthShell>
    );
  }

  if (!inviteInfo && message?.type === 'error') {
    return (
      <AuthShell
        title="Invalid Invitation"
        subtitle="This invitation link cannot be used."
        footer={
          <button type="button" className="btn-ghost auth-text-link" onClick={() => navigate('/login')}>
            Back to sign in
          </button>
        }
      >
        <div className="msg error">{message.text}</div>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Join Workspace"
      subtitle={
        inviteInfo
          ? `You have been invited as a ${inviteInfo.role || 'member'}. Create your account to accept.`
          : 'Set up your account to join the procurement workspace.'
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
      {message && <div className={`msg ${message.type}`}>{message.text}</div>}

      {!success && (
        <form onSubmit={handleSubmit} className="auth-form">
          <div className="form-group">
            <label className="form-label" htmlFor="accept-email">Email address</label>
            <input
              id="accept-email"
              className="input-field input-plain"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              autoComplete="email"
              required
              placeholder="you@company.com"
            />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="accept-password">Password</label>
            <input
              id="accept-password"
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
            <label className="form-label" htmlFor="accept-confirm">Confirm password</label>
            <input
              id="accept-confirm"
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
            {loading ? 'Creating account...' : 'Accept invite & create account'}
          </button>
        </form>
      )}

      {success && (
        <div style={{ marginTop: '1.5rem', textAlign: 'center' }}>
          <button type="button" className="btn btn-primary" onClick={() => navigate('/login')}>
            Go to sign in
          </button>
        </div>
      )}
    </AuthShell>
  );
}
