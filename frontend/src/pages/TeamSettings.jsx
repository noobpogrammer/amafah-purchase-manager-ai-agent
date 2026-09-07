import React, { useEffect, useState } from 'react';
import { supabase } from '../supabaseClient';
import { inviteUser } from '../api';
import { Copy, UserPlus, Users, Mail } from 'lucide-react';

export default function TeamSettings({ navigate }) {
  const [profile, setProfile] = useState(null);
  const [invitations, setInvitations] = useState([]);
  const [teamMembers, setTeamMembers] = useState([]);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('member');
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(false);

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
    const [invsRes, membersRes] = await Promise.all([
      supabase.from('invitations').select('*').eq('client_id', clientId).is('accepted_at', null).order('created_at', { ascending: false }),
      supabase.from('profiles').select('*').eq('client_id', clientId).order('created_at', { ascending: false }),
    ]);

    if (invsRes.error) console.error('Error fetching invitations:', invsRes.error);
    if (membersRes.error) console.error('Error fetching members:', membersRes.error);

    setInvitations(invsRes.data || []);
    setTeamMembers(membersRes.data || []);
  };

  useEffect(() => {
    loadData();
  }, [navigate]);

  const handleInvite = async (e) => {
    e.preventDefault();
    setMessage(null);
    setLoading(true);
    try {
      const res = await inviteUser({ email, role });
      setMessage({
        type: 'success',
        text: `Invitation email sent to ${email}. The user will receive an email from Supabase to accept and join the team.`,
      });
      setEmail('');
      await loadData();
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

      {invitations.length > 0 && (
        <div className="card">
          <div className="card-header">
            <h3 className="card-title flex-items">
              <Mail size={18} />
              Pending invitations ({invitations.length})
            </h3>
          </div>
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
        </div>
      )}
    </div>
  );
}

