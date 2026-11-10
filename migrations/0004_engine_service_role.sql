-- The engine writes through the Data API with the service_role key (schema `kaibot` is in the exposed schemas).
-- Only service_role gets the schema; anon and authenticated get nothing (the dashboard uses kb_* functions).
grant usage on schema kaibot to service_role;
grant select, insert, update, delete on all tables in schema kaibot to service_role;
grant usage, select on all sequences in schema kaibot to service_role;
alter default privileges in schema kaibot grant select, insert, update, delete on tables to service_role;
alter default privileges in schema kaibot grant usage, select on sequences to service_role;
revoke all on schema kaibot from anon, authenticated;
