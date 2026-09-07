import { supabase } from '../supabaseClient';

export async function ensureProfile() {
  const { data: userRes } = await supabase.auth.getUser();
  const user = userRes?.user ?? null;
  if (!user) return null;

  // check for existing profile
  const { data: existing, error: selErr } = await supabase.from('profiles').select('id, role, client_id').eq('id', user.id).maybeSingle();
  if (selErr) throw selErr;
  if (existing) return existing;

  // If invited via Supabase admin invite, user metadata contains client_id and role
  const metaClientId = user.user_metadata?.client_id || user.app_metadata?.client_id;
  const metaRole = user.user_metadata?.role || user.app_metadata?.role || 'member';
  if (metaClientId) {
    const { data: inserted, error: insErr } = await supabase
      .from('profiles')
      .insert({
        id: user.id,
        client_id: metaClientId,
        role: metaRole,
        email: user.email,
      })
      .select('id, role, client_id')
      .maybeSingle();
    if (!insErr && inserted) return inserted;
  }

  const pendingToken = sessionStorage.getItem('pending_invite_token');
  if (pendingToken) {
    const { data, error } = await supabase.rpc('accept_invitation', { p_token: pendingToken });
    sessionStorage.removeItem('pending_invite_token');
    if (error) throw error;
    return data;
  }

  const { data, error } = await supabase.rpc('claim_bootstrap_admin');
  if (error) throw error;
  return data;
}

