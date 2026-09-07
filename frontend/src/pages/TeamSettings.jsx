import React, { useEffect, useState } from 'react';
import { supabase } from '../supabaseClient';
import { createInviteToken } from '../api';
import { Copy, UserPlus, Users, Link as LinkIcon, Check } from 'lucide-react';

export default function TeamSettings({ navigate }) {
  const [profile, setProfile] = useState(null);
  const [pendingTokens, setPendingTokens] = useState([]);
  const [teamMembers, setTeamMembers] = useState([]);
  const [role, setRole] = useState('member');
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [latestInviteLink, setLatestInviteLink] = useState(null);
  const [copied, setCopied] = useState(false);

  const loadData = async () => {
    const sessionRes = await supabase.auth.getSession();
    const user = sessionRes?.data?.session?.user;
    if (!user) return navigate('/login');

    const { data, error } = await supabase.from('profiles').select('*').eq('id', user.id).maybeSingle();
    if (error) return setMessage({ type: 'error', text: error.message });
    setProfile(data);
    if (!data) return setMessage({ type: 'error', text: 'No profile found' });
    if (data.role !== 'admin') return setMessage({ type: 'error', text: 'Admin access required' });

    const clientId = data.client_id;
    const [tokensRes, membersRes] = await Promise.all([
      supabase
        .from('invite_tokens')
        .select('*')
        .eq('client_id', clientId)
        .is('used_at', null)
        .order('created_at', { ascending: false }),
      supabase.from('profiles').select('*').eq('client_id', clientId).order('created_at', { ascending: false }),
    ]);

    if (tokensRes.error) console.error('Error fetching invite tokens:', tokensRes.error);
    if (membersRes.error) console.error('Error fetching members:', membersRes.error);

    setPendingTokens(tokensRes.data || []);
    setTeamMembers(membersRes.data || []);
  };

  useEffect(() => {
    loadData();

    const handleFocus = () => {
      loadData();
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        loadData();
      }
    };

    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [navigate]);

  const handleGenerateLink = async (e) => {
    e.preventDefault();
    setMessage(null);
    setLoading(true);
    setCopied(false);
    try {
      const res = await createInviteToken({ role });
      const inviteUrl = `${window.location.origin}/accept-invite?token=${res.token}`;
      setLatestInviteLink(inviteUrl);
      setMessage({
        type: 'success',
        text: 'Invitation link generated! Copy and send it to your teammate.',
      });
      await loadData();
    } catch (err) {
      setMessage({ type: 'error', text: err.message || String(err) });
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
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
        <div className="error-alert">You need admin access to generate team invitations.</div>
      )}

      <div className="card">
        <div className="card-header">
          <h3 className="card-title flex-items">
            <UserPlus size={18} />
            Generate invite link
          </h3>
        </div>
        <form onSubmit={handleGenerateLink} className="auth-form">
          <div className="form-row">
            <div className="form-group">
              <label className="form-label" htmlFor="invite-role">Assign Role</label>
              <select
                id="invite-role"
                className="input-field input-plain select-input"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                disabled={!profile || profile.role !== 'admin'}
              >
                <option value="member">Member (Regular access)</option>
                <option value="admin">Admin (Manage team & settings)</option>
              </select>
            </div>
          </div>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={loading || !profile || profile.role !== 'admin'}
          >
            {loading ? 'Generating...' : 'Create shareable invite link'}
          </button>
        </form>

        {latestInviteLink && (
          <div style={{ marginTop: '1.25rem', padding: '1rem', background: 'rgba(255,255,255,0.03)', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.1)' }}>
            <label className="form-label flex-items" style={{ marginBottom: '0.5rem', color: '#10b981' }}>
              <LinkIcon size={16} /> Shareable Invite Link:
            </label>
            <div className="invite-link-row">
              <input
                className="input-field input-plain"
                readOnly
                value={latestInviteLink}
                onFocus={(e) => e.target.select()}
              />
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => copyToClipboard(latestInviteLink)}
              >
                {copied ? <><Check size={14} /> Copied</> : <><Copy size={14} /> Copy link</>}
              </button>
            </div>
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary, #888)', marginTop: '0.5rem' }}>
              This link is single-use and valid for 7 days. Once the user creates their account, they will automatically be assigned to this workspace.
            </p>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-header">
          <h3 className="card-title flex-items">
            <Users size={18} />
            Active team members ({teamMembers.length})
          </h3>
        </div>
        {teamMembers.length === 0 ? (
          <div className="empty-state">No team members found.</div>
        ) : (
          <ul className="invite-list">
            {teamMembers.map((member) => (
              <li key={member.id} className="supplier-matched-chip">
                <div>
                  <strong>{member.email || member.id}</strong>
                  <span className="phone-sub">{member.role}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {pendingTokens.length > 0 && (
        <div className="card">
          <div className="card-header">
            <h3 className="card-title flex-items">
              <LinkIcon size={18} />
              Active invitation links ({pendingTokens.length})
            </h3>
          </div>
          <ul className="invite-list">
            {pendingTokens.map((tok) => {
              const url = `${window.location.origin}/accept-invite?token=${tok.token}`;
              return (
                <li key={tok.id} className="supplier-matched-chip">
                  <div style={{ width: '100%' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                      <span className="phone-sub">Role: {tok.role}</span>
                      <span className="phone-sub" style={{ fontSize: '0.75rem' }}>
                        Created: {new Date(tok.created_at).toLocaleDateString()}
                      </span>
                    </div>
                    <div className="invite-link-row">
                      <input
                        className="input-field input-plain"
                        readOnly
                        value={url}
                        onFocus={(e) => e.target.select()}
                      />
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => copyToClipboard(url)}
                      >
                        <Copy size={14} /> Copy
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}


