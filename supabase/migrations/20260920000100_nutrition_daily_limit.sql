-- Run in the Supabase SQL editor BEFORE redeploying nutrition-chat.
begin;

create table if not exists public.nutrition_daily_usage (
    user_id uuid not null references auth.users(id) on delete cascade,
    usage_date date not null,
    request_count integer not null check (request_count between 1 and 10),
    primary key (user_id, usage_date)
);
alter table public.nutrition_daily_usage enable row level security;
revoke all on public.nutrition_daily_usage from public, anon, authenticated;
grant select, insert, update on public.nutrition_daily_usage to service_role;

create or replace function public.consume_nutrition_daily_quota(p_user_id uuid)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    current_day date := (statement_timestamp() at time zone 'UTC')::date;
    reset_time timestamptz := ((current_day + 1)::timestamp at time zone 'UTC');
    used integer;
begin
    if p_user_id is null then
        raise exception 'User ID is required';
    end if;

    -- The primary key and conditional UPSERT serialize concurrent requests.
    -- No read-then-write race: the 11th request cannot increment the counter.
    insert into public.nutrition_daily_usage as usage (user_id, usage_date, request_count)
    values (p_user_id, current_day, 1)
    on conflict (user_id, usage_date) do update
        set request_count = usage.request_count + 1
        where usage.request_count < 10
    returning request_count into used;

    return jsonb_build_object(
        'allowed', used is not null,
        'limit', 10,
        'remaining', case when used is null then 0 else 10 - used end,
        'resets_at', reset_time,
        'retry_after_seconds', greatest(1, ceil(extract(epoch from (reset_time - statement_timestamp())))::integer)
    );
end;
$$;

-- Only the backend may charge a quota; it supplies the verified Auth user ID.
revoke all on function public.consume_nutrition_daily_quota(uuid) from public, anon, authenticated;
grant execute on function public.consume_nutrition_daily_quota(uuid) to service_role;
notify pgrst, 'reload schema';
commit;
