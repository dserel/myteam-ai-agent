-- ============================================================
-- myTeam AI Agent — Supabase schema (Phase B: feedback loop)
-- Τρέχει στο Supabase → SQL Editor → New query
-- ============================================================

create extension if not exists pgcrypto;

-- ----------------------------------------------------------------
-- query_history: κάθε ερώτηση + SQL + αποτέλεσμα + feedback
-- ----------------------------------------------------------------
create table if not exists public.query_history (
    id              uuid primary key default gen_random_uuid(),
    created_at      timestamptz not null default now(),
    -- Identity
    username        text not null,
    tenant_club_id  bigint not null,
    user_role       text not null check (user_role in ('manager','parent')),
    tenant_user_id  bigint,
    -- Question/answer
    question        text not null,
    generated_sql   text,
    validated_sql   text,
    row_count       integer,
    answer          text,
    -- Diagnostics
    rejected_reason text,
    error           text,
    elapsed_ms      integer,
    -- Feedback
    feedback        smallint check (feedback in (-1, 0, 1)),  -- -1 thumbs down, 1 thumbs up, 0 neutral
    feedback_note   text,
    corrected_sql   text,
    is_golden       boolean not null default false
);

create index if not exists query_history_created_at_idx on public.query_history (created_at desc);
create index if not exists query_history_feedback_idx   on public.query_history (feedback);
create index if not exists query_history_golden_idx     on public.query_history (is_golden) where is_golden = true;
create index if not exists query_history_club_idx       on public.query_history (tenant_club_id);

-- ----------------------------------------------------------------
-- golden_examples: curated (question -> SQL) ζεύγη
-- Έρχονται είτε από upvoted query_history είτε από manual entry.
-- Στη φάση Γ θα προστεθεί embedding column για semantic retrieval.
-- ----------------------------------------------------------------
create table if not exists public.golden_examples (
    id              uuid primary key default gen_random_uuid(),
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    -- ποιος ρόλος αφορά
    user_role       text not null check (user_role in ('manager','parent','any')),
    question        text not null,
    sql_template    text not null,        -- με placeholders αν χρειάζεται
    explanation     text,
    tags            text[],               -- π.χ. {'attendance','financial'}
    source_history_id uuid references public.query_history(id) on delete set null,
    enabled         boolean not null default true
);

create index if not exists golden_examples_enabled_idx on public.golden_examples (enabled);
create index if not exists golden_examples_role_idx    on public.golden_examples (user_role);

-- ----------------------------------------------------------------
-- schema_annotations: user-supplied notes που επιστρέφονται
-- στο context του LLM (Φάση Γ θα τα κάνει inject αυτόματα).
-- ----------------------------------------------------------------
create table if not exists public.schema_annotations (
    id              uuid primary key default gen_random_uuid(),
    created_at      timestamptz not null default now(),
    -- Target
    table_name      text not null,
    column_name     text,                  -- NULL = αφορά όλο τον πίνακα
    -- Content
    note            text not null,
    severity        text not null default 'info' check (severity in ('info','warn','critical')),
    enabled         boolean not null default true,
    created_by      text
);

create index if not exists schema_annotations_table_idx on public.schema_annotations (table_name);
create index if not exists schema_annotations_enabled_idx on public.schema_annotations (enabled);

-- ----------------------------------------------------------------
-- Helpful view: latest 100 conversations με feedback
-- ----------------------------------------------------------------
create or replace view public.v_recent_questions as
select
    id,
    created_at,
    username,
    user_role,
    tenant_club_id,
    question,
    row_count,
    elapsed_ms,
    feedback,
    is_golden,
    case
        when rejected_reason is not null then 'rejected'
        when error is not null            then 'errored'
        when row_count = 0                then 'empty'
        else 'ok'
    end as status
from public.query_history
order by created_at desc
limit 100;

-- ----------------------------------------------------------------
-- Row Level Security (φτιάχνεται για multi-tenant μελλοντικά)
-- Για το single-tenant MVP, αφήνουμε disabled.
-- ----------------------------------------------------------------
-- alter table public.query_history     enable row level security;
-- alter table public.golden_examples   enable row level security;
-- alter table public.schema_annotations enable row level security;
