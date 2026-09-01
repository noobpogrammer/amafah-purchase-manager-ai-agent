import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseKey = import.meta.env.VITE_SUPABASE_KEY;

export const supabase = createClient(supabaseUrl, supabaseKey);
export const DEMO_CLIENT_ID = import.meta.env.VITE_DEMO_CLIENT_ID || 'd88c52ad-3d0b-42e9-86f1-b9f70018856b';
export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
