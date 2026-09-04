import { supabase } from '../supabaseClient';

export async function ensureProfile() {
  const { data: userRes } = await supabase.auth.getUser();
  const user = userRes?.user ?? null;
  if (!user) return null;

  // check for existing profile
  const { data: existing, error: selErr } = await supabase.from('profiles').select('id, role, client_id').eq('id', user.id).maybeSingle();
  if (selErr) throw selErr;
  if (existing) return existing;

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
