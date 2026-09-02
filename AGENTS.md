# Project Rules & Guidelines for Agents

## Database Schema & Migrations Rule
Never edit `schema.sql` directly as the source of truth. All schema changes MUST go through `supabase migration new <descriptive_name>` + `supabase db push`. After creating a migration, verify it applied successfully by querying the live schema before considering the task done.

### Migration Steps:
1. Create a migration file:
   ```bash
   supabase migration new <descriptive_name>
   ```
2. Place SQL changes inside `supabase/migrations/<timestamp>_<descriptive_name>.sql`.
3. Push changes directly to the remote Supabase database:
   ```bash
   supabase db push
   ```
