-- Add nullable human_response column to flagged_for_review table
alter table flagged_for_review add column if not exists human_response text;
