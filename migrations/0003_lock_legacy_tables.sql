-- Old bloodbath tables in public (decision 10): RLS on with no policies, and no grants for anon/authenticated,
-- so the dashboard's public key can neither read nor discover them. Nothing is deleted.
alter table public.agent_goal_snapshots enable row level security;
alter table public.decision_engine_meta enable row level security;
alter table public.decision_memory enable row level security;
alter table public.manual_ticker_checks enable row level security;
alter table public.portfolio enable row level security;
alter table public.position_snapshots enable row level security;
alter table public.recommendation_signals enable row level security;
alter table public.recommendation_trace enable row level security;
alter table public.trade_decisions enable row level security;
alter table public.transactions enable row level security;
alter table public.worker_runs enable row level security;
revoke all on table public.agent_goal_snapshots, public.decision_engine_meta, public.decision_memory,
  public.manual_ticker_checks, public.portfolio, public.position_snapshots, public.recommendation_signals,
  public.recommendation_trace, public.trade_decisions, public.transactions, public.worker_runs
  from anon, authenticated;
