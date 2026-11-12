-- 0015: one trial registry (M7 S86, issue #126; decision 15 section 7; audit F5).
--
-- NOT APPLIED. Production migrations are the stakeholder's: apply this one on "apply". Nothing needs it before
-- then: the gate reports read the registry file and the older kaibot.trial table, and skip this table while it
-- does not exist.
--
-- kaibot.trial_registry: one row per research RUN (replay, walk-forward, ablation, config replay).
-- `n_trials` is the number of CONFIGURATIONS that run searched. Every consumer counts configurations, the sum
-- of n_trials over the distinct trial ids, not rows (kaibot.evaluation.trials.count_configurations): the
-- walk-forward scripts, the trial report, the M4 gate report and gate v2.
--
-- The first copy stays data/trials.jsonl on the research machine. `scripts/trial_registry.py push` copies the
-- rows this table lacks, so an engine in a container or in the cloud reads the same registry without a mounted
-- file. The reader merges file and table by trial_id: a run that is in both counts once.
--
-- Append-only, like the gate tables of 0010: nobody (engine, owner, dashboard) can update, delete or truncate.
-- A trial that was run stays counted. The older kaibot.trial table (0001) is left as it is and is still read.

create table if not exists kaibot.trial_registry (
  id         bigint generated always as identity primary key,
  logged_at  timestamptz not null default now(),                 -- when the row reached the database
  trial_id   text        not null unique check (length(trial_id) between 1 and 300),
  ts         timestamptz,                                         -- when the run happened (research machine)
  strategy   text        not null check (length(strategy) between 1 and 200),
  n_trials   integer     not null default 1 check (n_trials >= 1),  -- configurations this run searched
  "row"      jsonb       not null                                 -- the registry row as written to the file
);
create index if not exists trial_registry_strategy on kaibot.trial_registry (strategy);

alter table kaibot.trial_registry enable row level security;      -- no policies: only service_role (bypasses RLS)
-- 0004's default privileges give service_role update and delete on every new table: take everything back first.
revoke all on kaibot.trial_registry from public, anon, authenticated, service_role;
grant select, insert on kaibot.trial_registry to service_role;

create or replace function kaibot.forbid_trial_mutation() returns trigger language plpgsql set search_path = '' as $$
begin
  raise exception '% is append-only (S86: a trial that was run stays counted)', tg_table_name using errcode = '42501';
end $$;
drop trigger if exists trial_registry_append_only on kaibot.trial_registry;
create trigger trial_registry_append_only before update or delete on kaibot.trial_registry
  for each row execute function kaibot.forbid_trial_mutation();
drop trigger if exists trial_registry_no_truncate on kaibot.trial_registry;
create trigger trial_registry_no_truncate before truncate on kaibot.trial_registry
  for each statement execute function kaibot.forbid_trial_mutation();
