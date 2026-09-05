import React, { useEffect, useState } from 'react';
import { supabase } from '../supabaseClient';
import { Copy, UserPlus } from 'lucide-react';

function hexEncode(bytes) {
  return Array.from(new Uint8Array(bytes)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

export default function TeamSettings({ navigate }) {
  const [profile, setProfile] = useState(null);
  const [invitations, setInvitations] = useState([]);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('member');
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      const sessionRes = await supabase.auth.getSession();
      const user = sessionRes?.data?.session?.user;
      if (!user) return navigate('/login');

      const { data, error } = await supabase.from('profiles').select('*').eq('id', user.id).maybeSingle();
      if (error) return setMessage({ type: 'error', text: error.message });
      setProfile(data);
      if (!data) return setMessage({ type: 'error', text: 'No profile found' });
      if (data.role !== 'admin') return setMessage({ type: 'error', text: 'Admin access required' });

      const clientId = data.client_id;
      const { data: invs, error: invErr } = await supabase.from('invitations').select('*').eq('client_id', clientId).is('accepted_at', null);
      if (invErr) return setMessage({ type: 'error', text: invErr.message });
      setInvitations(invs || []);
    })();
  }, [navigate]);

  const handleInvite = async (e) => {
    e.preventDefault();
    setMessage(null);
    setLoading(true);
    try {
      const raw = new Uint8Array(32);
      crypto.getRandomValues(raw);
      const rawHex = hexEncode(raw);
      const enc = new TextEncoder();
      const dataToHash = enc.encode(rawHex);
      const digest = await crypto.subtle.digest('SHA-256', dataToHash);
      const token_hash = hexEncode(digest);

      const sessionRes = await supabase.auth.getSession();
      const user = sessionRes?.data?.session?.user;
      if (!user) throw new Error('No user session');

      const { data: profileRow } = await supabase.from('profiles').select('client_id').eq('id', user.id).maybeSingle();
      const clientId = profileRow?.client_id;
      if (!clientId) throw new Error('No client_id on profile');

      const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
      const { error } = await supabase.from('invitations').insert([{ client_id: clientId, email, role, token_hash, expires_at: expiresAt, invited_by: user.id }]);
      if (error) throw error;
      setMessage({ type: 'success', text: 'Invitation created. Copy the link below — it is shown only once.' });
      const inviteLink = `${window.location.origin}/signup?invite=${rawHex}`;
      setInvitations((prev) => prev.concat([{ email, role, inviteLink }]));
      setEmail('');
    } catch (err) {
      setMessage({ type: 'error', text: err.message || String(err) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="view-container">
      <div className="view-header">
        <div>
          <h2 className="view-title">Team & invites</h2>
          <p className="view-description">Invite teammates to this procurement workspace. Admins only.</p>
        </div>
      </div>

      {message && <div className={`msg ${message.type}`}>{message.text}</div>}

      {profile && profile.role !== 'admin' && (
        <div className="error-alert">You need admin access to send invitations.</div>
      )}

      <div className="card">
        <div className="card-header">
          <h3 className="card-title flex-items">
            <UserPlus size={18} />
            Invite teammate
          </h3>
        </div>
        <form onSubmit={handleInvite} className="auth-form">
          <div className="form-row">
            <div className="form-group">
              <label className="form-label" htmlFor="invite-email">Email</label>
              <input
                id="invite-email"
                className="input-field input-plain"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                type="email"
                required
                disabled={!profile || profile.role !== 'admin'}
              />
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="invite-role">Role</label>
              <select
                id="invite-role"
                className="input-field input-plain select-input"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                disabled={!profile || profile.role !== 'admin'}
              >
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
            </div>
          </div>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={loading || !profile || profile.role !== 'admin'}
          >
            {loading ? 'Inviting...' : 'Send invite'}
          </button>
        </form>
      </div>

      <div className="card">
        <div className="card-header">
          <h3 className="card-title">Pending invitations</h3>
        </div>
        {invitations.length === 0 ? (
          <div className="empty-state">No pending invitations.</div>
        ) : (
          <ul className="invite-list">
            {invitations.map((inv, i) => (
              <li key={i} className="supplier-matched-chip">
                <div>
                  <strong>{inv.email}</strong>
                  <span className="phone-sub">{inv.role}</span>
                  {inv.inviteLink && (
                    <div className="invite-link-row">
                      <input
                        className="input-field input-plain"
                        readOnly
                        value={inv.inviteLink}
                        onFocus={(e) => e.target.select()}
                      />
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => navigator.clipboard.writeText(inv.inviteLink)}
                      >
                        <Copy size={14} /> Copy
                      </button>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
