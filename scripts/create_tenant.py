"""
CLI to provision a new tenant end-to-end:
  1. Creates the tenant row in public.tenants
  2. Creates the isolated PostgreSQL schema via create_tenant_schema()
  3. Seeds skills_config based on the chosen plan

Usage:
    python scripts/create_tenant.py \
        --name "Farmácia ABC" \
        --callback-url "https://api.mywaha.com/webhook/abc" \
        --plan pro
"""
import argparse
import asyncio
import os
import re
import secrets
import sys

import asyncpg
from dotenv import load_dotenv


def make_schema_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "_", name.lower())[:40]
    return f"tenant_{slug}"


PLAN_SKILLS = {
    "basic": [
        ("farmaceutico", "claude-sonnet-4-6", "anthropic"),
    ],
    "pro": [
        ("farmaceutico",    "claude-sonnet-4-6",          "anthropic"),
        ("principio_ativo", "claude-sonnet-4-6",          "anthropic"),
        ("genericos",       "gemini-2.0-flash",           "google"),
        ("vendedor",        "claude-sonnet-4-6",          "anthropic"),
    ],
    "enterprise": [
        ("farmaceutico",    "claude-sonnet-4-6",          "anthropic"),
        ("principio_ativo", "claude-sonnet-4-6",          "anthropic"),
        ("genericos",       "gemini-2.0-flash",           "google"),
        ("vendedor",        "claude-sonnet-4-6",          "anthropic"),
        ("recuperador",     "claude-haiku-4-5-20251001",  "anthropic"),
    ],
}


async def provision(name: str, callback_url: str, plan: str, database_url: str) -> None:
    if plan not in PLAN_SKILLS:
        print(f"Unknown plan '{plan}'. Valid: {list(PLAN_SKILLS)}")
        sys.exit(1)

    schema_name = make_schema_name(name)
    api_key = secrets.token_urlsafe(32)

    conn = await asyncpg.connect(database_url)
    try:
        # Create tenant row
        row = await conn.fetchrow(
            """
            INSERT INTO public.tenants (name, api_key, callback_url, plan, schema_name)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            name,
            api_key,
            callback_url,
            plan,
            schema_name,
        )
        tenant_id = str(row["id"])

        # Create isolated schema
        await conn.execute("SELECT create_tenant_schema($1)", schema_name)

        # Migration 010: ensure cart has sales_attempts column for this fresh schema
        await conn.execute("SELECT public.add_sales_attempts_to_cart($1)", schema_name)

        # Default sales config row (required customer fields, retry policy)
        await conn.execute(
            "INSERT INTO public.tenant_sales_config (tenant_id) VALUES ($1) ON CONFLICT DO NOTHING",
            tenant_id,
        )

        # Seed skills
        await conn.execute(f"SET search_path = {schema_name}, public")
        for skill_name, llm_model, llm_provider in PLAN_SKILLS[plan]:
            await conn.execute(
                """
                INSERT INTO skills_config (skill_name, ativo, llm_model, llm_provider)
                VALUES ($1, TRUE, $2, $3)
                ON CONFLICT (skill_name) DO NOTHING
                """,
                skill_name,
                llm_model,
                llm_provider,
            )

        print("\n✅ Tenant provisioned successfully")
        print(f"   ID:          {tenant_id}")
        print(f"   Name:        {name}")
        print(f"   Plan:        {plan}")
        print(f"   Schema:      {schema_name}")
        print(f"   API Key:     {api_key}")
        print(f"   Callback:    {callback_url}")
        print(f"   Skills:      {[s[0] for s in PLAN_SKILLS[plan]]}")

    finally:
        await conn.close()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Provision a new pharmacy tenant")
    parser.add_argument("--name", required=True, help="Pharmacy name")
    parser.add_argument("--callback-url", required=True, help="WhatsApp gateway callback URL")
    parser.add_argument("--plan", default="basic", choices=["basic", "pro", "enterprise"])
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL env var is required")
        sys.exit(1)

    asyncio.run(
        provision(
            name=args.name,
            callback_url=args.callback_url,
            plan=args.plan,
            database_url=database_url,
        )
    )


if __name__ == "__main__":
    main()
