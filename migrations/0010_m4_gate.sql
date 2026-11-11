-- 0010: M4 evaluation gate (stories S38, S41).
-- gate_random: the nightly random-entry baseline (1,000 totals per day), first write wins.
-- gate_report: every computed report, append-only. Nobody (engine, owner, dashboard) can update or delete either:
-- the verdict "can't be bent". The dashboard reads only through kb_gate_report (owner-checked).

create table if not exists kaibot.gate_random (
  run_id      bigint not null references kaibot.run(id),
  session     date not null,
  strategy    text not null,
  fingerprint text not null,
  n_trades    integer not null,
  seed        bigint not null,
  totals      double precision[] not null check (cardinality(totals) between 1 and 10000),
  created_at  timestamptz not null default now(),
  primary key (run_id, session, strategy)
);

create table if not exists kaibot.gate_report (
  id          bigint generated always as identity primary key,
  ts          timestamptz not null default now(),
  run_id      bigint not null references kaibot.run(id),
  session     date not null,
  bar_sha     text not null,
  fingerprint text not null,
  overall     text not null check (overall in ('discuss', 'keep collecting', 'kill')),
  report      jsonb not null
);
create index if not exists gate_report_run_ts on kaibot.gate_report (run_id, ts desc);

alter table kaibot.gate_random enable row level security;
alter table kaibot.gate_report enable row level security;
revoke all on kaibot.gate_random, kaibot.gate_report from public, anon, authenticated;
grant select, insert on kaibot.gate_random, kaibot.gate_report to service_role;

create or replace function kaibot.forbid_gate_mutation() returns trigger language plpgsql set search_path = '' as $$
begin
  raise exception '% is append-only (S38: the gate cannot be bent)', tg_table_name using errcode = '42501';
end $$;
drop trigger if exists gate_random_append_only on kaibot.gate_random;
create trigger gate_random_append_only before update or delete on kaibot.gate_random
  for each row execute function kaibot.forbid_gate_mutation();
drop trigger if exists gate_report_append_only on kaibot.gate_report;
create trigger gate_report_append_only before update or delete on kaibot.gate_report
  for each row execute function kaibot.forbid_gate_mutation();
drop trigger if exists gate_report_no_truncate on kaibot.gate_report;
create trigger gate_report_no_truncate before truncate on kaibot.gate_report
  for each statement execute function kaibot.forbid_gate_mutation();
drop trigger if exists gate_random_no_truncate on kaibot.gate_random;
create trigger gate_random_no_truncate before truncate on kaibot.gate_random
  for each statement execute function kaibot.forbid_gate_mutation();

-- S41: the latest report for a run plus a short history of verdicts (newest first). Read-only.
create or replace function public.kb_gate_report(p_run bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return jsonb_build_object(
    'latest', (select jsonb_build_object('id', g.id, 'ts', g.ts, 'session', g.session, 'bar_sha', g.bar_sha,
                 'fingerprint', g.fingerprint, 'overall', g.overall, 'report', g.report)
               from kaibot.gate_report g where g.run_id = p_run order by g.ts desc, g.id desc limit 1),
    'history', coalesce((select jsonb_agg(h order by h.ts desc) from (
        select g.id, g.ts, g.session, g.overall, g.fingerprint,
               (select jsonb_object_agg(k, v->>'status') from jsonb_each(g.report->'strategies') as s(k, v)) as status,
               (select jsonb_object_agg(k, (v->>'n_trades')::int) from jsonb_each(g.report->'strategies') as s(k, v))
                 as n_trades
        from kaibot.gate_report g where g.run_id = p_run order by g.ts desc, g.id desc limit 60) h), '[]'::jsonb),
    'random_days', (select count(*) from kaibot.gate_random r where r.run_id = p_run)
  );
end $$;

revoke all on function public.kb_gate_report(bigint) from public, anon;
grant execute on function public.kb_gate_report(bigint) to authenticated;
