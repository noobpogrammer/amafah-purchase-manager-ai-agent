import { createClient } from '@supabase/supabase-js';

const supabaseUrl = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_SUPABASE_URL) || process.env.VITE_SUPABASE_URL || '';
// Use the public anon key in frontend code. Do NOT expose service_role keys via VITE_*.
const supabaseKey = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_SUPABASE_ANON_KEY) || process.env.VITE_SUPABASE_ANON_KEY || '';

let _createdSupabase;
try {
	_createdSupabase = createClient(supabaseUrl, supabaseKey);
} catch (e) {
	// If no env provided (e.g., running Node test), provide a minimal stub
	_createdSupabase = {
		auth: {
			async getSession() {
				return { data: { session: null } };
			},
			onAuthStateChange() {
				return { data: { subscription: null } };
			},
		},
		from() {
			return {
				select() {
					return {
						eq() {
							return {
								async single() {
									return { data: null, error: null };
								},
							};
						},
					};
				},
			};
		},
	};
}
export let supabase = _createdSupabase;
export const DEMO_CLIENT_ID = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_DEMO_CLIENT_ID) || process.env.VITE_DEMO_CLIENT_ID || 'd88c52ad-3d0b-42e9-86f1-b9f70018856b';
export const API_URL = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_API_URL) || process.env.VITE_API_URL || 'http://localhost:8000';

if (typeof window !== 'undefined' && API_URL.includes('localhost') && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
	console.error(
		`[Amafha] VITE_API_URL is "${API_URL}" but this page is served from ${window.location.origin}. ` +
			'Set VITE_API_URL to the backend public Railway URL at frontend build time, then redeploy.'
	);
}

// In-memory session cache for client_id resolved from `profiles`.
let cachedClientId = null;
let cachedUserId = null;
let inFlightPromise = null;
let profileFetchCount = 0; // increments each time we actually query `profiles`

// Internal supabase reference that can be swapped for tests
let _supabase = supabase;

export function setSupabaseClientForTest(mock) {
	_supabase = mock;
}

export function clearClientIdCache() {
	cachedClientId = null;
	cachedUserId = null;
	inFlightPromise = null;
}

export function getProfileFetchCount() {
	return profileFetchCount;
}

// Resolve the current user's client_id from the `profiles` table.
// This should be used by frontend code to scope UI queries. It returns null
// when there is no authenticated user or no profile entry.
export async function getCurrentClientId() {
	try {
		const sessionRes = await _supabase.auth.getSession();
		const userId = sessionRes?.data?.session?.user?.id;
		if (!userId) return null;

		// Return cached value when user matches
		if (cachedUserId === userId && cachedClientId) return cachedClientId;

		// Deduplicate concurrent fetches
		if (inFlightPromise) return await inFlightPromise;

		inFlightPromise = (async () => {
			profileFetchCount += 1;
			try {
				const { data, error } = await _supabase.from('profiles').select('client_id').eq('id', userId).single();
				if (error || !data) {
					cachedClientId = null;
					cachedUserId = userId;
					inFlightPromise = null;
					return null;
				}
				cachedClientId = data.client_id || null;
				cachedUserId = userId;
				inFlightPromise = null;
				return cachedClientId;
			} catch (err) {
				cachedClientId = null;
				cachedUserId = userId;
				inFlightPromise = null;
				return null;
			}
		})();

		return await inFlightPromise;
	} catch (e) {
		return null;
	}
}

// Helper to call backend API with the current Supabase session access token when available.
export async function apiFetch(path, options = {}) {
	const sessionRes = await supabase.auth.getSession();
	const token = sessionRes?.data?.session?.access_token;
	const headers = Object.assign({}, options.headers || {});
	if (token) {
		headers['Authorization'] = `Bearer ${token}`;
	}
	return fetch(`${API_URL}${path}`, Object.assign({}, options, { headers }));
}

// Wire auth state changes to clear cache on sign-out or user switch
try {
  if (supabase && supabase.auth && typeof supabase.auth.onAuthStateChange === 'function') {
    supabase.auth.onAuthStateChange((event, session) => {
      if (event === 'SIGNED_OUT' || event === 'USER_UPDATED' || event === 'USER_DELETED') {
        clearClientIdCache();
      }
    });
  }
} catch (e) {
  // ignore in environments where onAuthStateChange isn't available
}
