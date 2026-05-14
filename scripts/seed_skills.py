"""
Seed skills_config for a tenant based on their plan.

Usage:
    python scripts/seed_skills.py <schema_name> <plan>

Example:
    python scripts/seed_skills.py tenant_farmacia_abc pro
"""
import asyncio
import sys
import asyncpg

PLAN_SKILLS = {
    "basic": [
        ("saudacao",     "claude-haiku-4-5-20251001",  "anthropic"),
        ("farmaceutico", "claude-sonnet-4-6",          "anthropic"),
    ],
    "pro": [
        ("saudacao",        "claude-haiku-4-5-20251001",  "anthropic"),
        ("farmaceutico",    "claude-sonnet-4-6",          "anthropic"),
        ("principio_ativo", "claude-sonnet-4-6",          "anthropic"),
        ("genericos",       "gemini-2.0-flash",           "google"),
        ("vendedor",        "claude-sonnet-4-6",          "anthropic"),
    ],
    "enterprise": [
        ("saudacao",        "claude-haiku-4-5-20251001",  "anthropic"),
        ("farmaceutico",    "claude-sonnet-4-6",          "anthropic"),
        ("principio_ativo", "claude-sonnet-4-6",          "anthropic"),
        ("genericos",       "gemini-2.0-flash",           "google"),
        ("vendedor",        "claude-sonnet-4-6",          "anthropic"),
        ("recuperador",     "claude-haiku-4-5-20251001",  "anthropic"),
    ],
}


async def seed(schema_name: str, plan: str, database_url: str) -> None:
    skills = PLAN_SKILLS.get(plan)
    if not skills:
        print(f"Unknown plan: {plan}. Valid: {list(PLAN_SKILLS)}")
        sys.exit(1)

    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(f"SET search_path = {schema_name}, public")

        for skill_name, llm_model, llm_provider in skills:
            await conn.execute(
                """
                INSERT INTO skills_config (skill_name, ativo, llm_model, llm_provider)
                VALUES ($1, TRUE, $2, $3)
                ON CONFLICT (skill_name) DO UPDATE
                SET ativo = TRUE, llm_model = EXCLUDED.llm_model,
                    llm_provider = EXCLUDED.llm_provider
                """,
                skill_name,
                llm_model,
                llm_provider,
            )
        print(f"Seeded {len(skills)} skills for schema={schema_name} plan={plan}")
    finally:
        await conn.close()


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()

    if len(sys.argv) < 3:
        print("Usage: python seed_skills.py <schema_name> <plan>")
        sys.exit(1)

    asyncio.run(
        seed(
            schema_name=sys.argv[1],
            plan=sys.argv[2],
            database_url=os.environ["DATABASE_URL"],
        )
    )
