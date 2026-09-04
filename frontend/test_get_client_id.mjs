import { setSupabaseClientForTest, getCurrentClientId, getProfileFetchCount, clearClientIdCache } from './src/supabaseClient.js';

// Mock supabase client for testing
const mockSupabase = {
  auth: {
    async getSession() {
      return { data: { session: { user: { id: 'user-1' } }, access_token: 'tok' } };
    },
    onAuthStateChange(cb) {
      // not used in this simple test
      this._cb = cb;
      return { data: { subscription: true } };
    }
  },
  from(table) {
    return {
      select() {
        return {
          eq() {
            return {
              async single() {
                // simulate DB returning client_id
                return { data: { client_id: 'client-xyz' }, error: null };
              }
            };
          }
        };
      }
    };
  }
};

setSupabaseClientForTest(mockSupabase);

async function run() {
  clearClientIdCache();
  console.log('initial profileFetchCount:', getProfileFetchCount());
  const a = await getCurrentClientId();
  console.log('first call clientId:', a);
  console.log('after first fetch profileFetchCount:', getProfileFetchCount());

  // multiple concurrent calls
  const [b, c, d] = await Promise.all([getCurrentClientId(), getCurrentClientId(), getCurrentClientId()]);
  console.log('concurrent results:', b, c, d);
  console.log('after concurrent calls profileFetchCount:', getProfileFetchCount());

  // simulate sign-out by clearing cache and changing mock session to no user
  clearClientIdCache();
  mockSupabase.auth.getSession = async () => ({ data: { session: null } });
  const e = await getCurrentClientId();
  console.log('after signout clientId:', e);
  console.log('final profileFetchCount:', getProfileFetchCount());
}

run().catch(console.error);
