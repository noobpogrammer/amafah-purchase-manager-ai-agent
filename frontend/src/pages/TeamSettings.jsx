import React, { useEffect, useState } from 'react';
import { supabase, getCurrentClientId } from '../supabaseClient';

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
  }, []);

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
      const { data, error } = await supabase.from('invitations').insert([{ client_id: clientId, email, role, token_hash, expires_at: expiresAt, invited_by: user.id }]);
      if (error) throw error;
      setMessage({ type: 'success', text: 'Invitation created. Copy the link below.' });
      const inviteLink = `${window.location.origin}/signup?invite=${rawHex}`;
      // Show invite link in UI (raw token is only shown once)
      setInvitations((prev) => prev.concat([{ email, role, inviteLink }]));
    } catch (err) {
      setMessage({ type: 'error', text: err.message || String(err) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="team-settings">
      <h2>Team Settings</h2>
      {message && <div className={`msg ${message.type}`}>{message.text}</div>}

      <section className="invite-form">
        <h3>Invite teammate</h3>
        <form onSubmit={handleInvite} className="auth-form">
          <label>Email</label>
          <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" required />
          <label>Role</label>
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="member">Member</option>
            <option value="admin">Admin</option>
          </select>
          <button type="submit" disabled={loading}>{loading ? 'Inviting...' : 'Invite'}</button>
        </form>
      </section>

      <section className="invitation-list">
        <h3>Pending invitations</h3>
        <ul>
          {invitations.map((inv, i) => (
            <li key={i}>
              <strong>{inv.email}</strong> — {inv.role} {inv.inviteLink && (<div>Invite link: <input readOnly value={inv.inviteLink} onFocus={(e)=>e.target.select()} /></div>)}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
