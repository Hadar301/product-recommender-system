import asyncio
import random
import string
from datetime import date
from pathlib import Path

import pandas as pd
import yaml
from sqlalchemy.future import select

from database.db import get_db
from database.models_sql import User
from services.feast.feast_service import FeastService
from services.security import hash_password


# Utility: generate random email and password
def generate_email(user_id: str) -> str:
    return f"user_{user_id[-6:]}@example.com"


def generate_password(length=10) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


async def seed_users():
    users: pd.DataFrame = FeastService().get_all_existing_users()

    # Filter out new users (27-digit numeric strings)
    users = users[
        ~(
            users["user_id"].astype(str).str.isdigit()
            & (users["user_id"].astype(str).str.len() == 27)
        )
    ]

    async for db in get_db():
        # Get existing user IDs from database
        result = await db.execute(select(User.user_id))
        existing_user_ids = {row[0] for row in result.fetchall()}

        # Create test users with known credentials for easy testing
        await _create_test_users(db, existing_user_ids)

        # Re-fetch existing user IDs after creating test users
        result = await db.execute(select(User.user_id))
        updated_existing_user_ids = {row[0] for row in result.fetchall()}

        # Filter out users that already exist in database (including test users)
        users_to_add = users[~users["user_id"].astype(str).isin(updated_existing_user_ids)]

        # Log what we're doing with Feast users
        feast_total = len(users)
        feast_filtered = len(users_to_add)
        feast_skipped = feast_total - feast_filtered
        print(
            f"📊 Feast users: {feast_filtered} to import, {feast_skipped} skipped (already exist)"
        )

        if feast_skipped > 0:
            skipped_ids = users[users["user_id"].astype(str).isin(updated_existing_user_ids)][
                "user_id"
            ].tolist()
            print(
                f"   Skipped Feast users: {skipped_ids[:5]}{'...' if len(skipped_ids) > 5 else ''}"
            )

        # Generate emails and passwords for remaining Feast users only
        users_to_add["email"] = users_to_add["user_id"].astype(str).apply(generate_email)
        users_to_add["password"] = users_to_add["user_id"].apply(lambda _: generate_password())

        # Create User objects in batch
        user_objects = (
            users_to_add.assign(
                user_id=lambda df: df["user_id"].astype(str),
                age=0,
                gender="unknown",
                signup_date=date.today(),
                preferences=lambda df: df["preferences"].fillna(""),
                hashed_password=lambda df: df["password"].apply(hash_password),
            )
            .apply(
                lambda row: User(
                    user_id=row["user_id"],
                    email=row["email"],
                    age=row["age"],
                    gender=row["gender"],
                    signup_date=row["signup_date"],
                    preferences=row["preferences"],
                    password=row["password"],
                    hashed_password=row["hashed_password"],
                ),
                axis=1,
            )
            .tolist()
        )

        # Add all users at once
        db.add_all(user_objects)
        await db.commit()


def _load_test_user_config():
    """Load test user configuration from YAML file"""
    config_path = Path(__file__).parent.parent / "config" / "test_users.yaml"
    try:
        if not config_path.exists():
            print(f"📁 Config file not found at {config_path}, using fallback configuration")
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, "r") as file:
            config = yaml.safe_load(file)

        # Validate required fields
        if "test_users" not in config or "config" not in config:
            raise ValueError("Invalid config structure: missing 'test_users' or 'config' section")

        print(f"📋 Loaded test user configuration from {config_path}")
        return config

    except Exception as e:
        print(f"⚠️  Could not load test user config from {config_path}: {e}")
        print("🔄 Using minimal fallback configuration")
        # Return minimal fallback config
        return {
            "test_users": [
                {
                    "email": "demo1@example.com",
                    "password": "demo123",
                    "age": 28,
                    "gender": "Female",
                    "preferences": "Electronics,Books",
                    "description": "Fallback test user",
                }
            ],
            "config": {
                "feast_users_to_select": 1,
                "fallback_user_ids": ["demo_shopper_1"],
            },
        }


async def _create_test_users(db, existing_user_ids: set):
    """
    Create test users with known credentials using REAL Feast user IDs.

    This ensures test users have both:
    - Known passwords for easy login
    - Rich interaction history from the Feast dataset for realistic recommendations

    These users take precedence over Feast imports - if a Feast user exists
    with the same ID, the Feast user will be skipped to preserve our test
    user data and known passwords.
    """

    # Load test user configuration
    config = _load_test_user_config()
    test_user_templates = config["test_users"]
    num_users_needed = config["config"]["feast_users_to_select"]
    fallback_ids = config["config"]["fallback_user_ids"]

    print(f"📋 Loading {len(test_user_templates)} test user templates from config")

    # Get real user IDs from Feast dataset
    try:
        feast_users = FeastService().get_all_existing_users()
        available_feast_ids = feast_users["user_id"].astype(str).tolist()
        print(f"🔍 Found {len(available_feast_ids)} real Feast user IDs")

        # Pick the required number of real user IDs for our test users
        selected_feast_ids = available_feast_ids[:num_users_needed]
        print(f"📋 Selected Feast user IDs for test users: {selected_feast_ids}")

    except Exception as e:
        print(f"⚠️  Could not fetch Feast user IDs: {e}")
        print(
            "🔄 Falling back to configured placeholder user IDs (will have no interaction history)"
        )
        selected_feast_ids = fallback_ids[:num_users_needed]

    # Ensure we have enough IDs
    if len(selected_feast_ids) < len(test_user_templates):
        print(
            f"⚠️  Only {len(selected_feast_ids)} Feast IDs available, "
            f"but {len(test_user_templates)} test users configured"
        )
        print(f"🔄 Will only create {len(selected_feast_ids)} test users")
        test_user_templates = test_user_templates[: len(selected_feast_ids)]

    # Map test user templates to real Feast user IDs
    test_users_data = []
    for i, template in enumerate(test_user_templates):
        test_user = template.copy()
        test_user["user_id"] = selected_feast_ids[i]
        test_users_data.append(test_user)

    # Only create test users that don't already exist
    for user_data in test_users_data:
        if user_data["user_id"] not in existing_user_ids:
            test_user = User(
                user_id=user_data["user_id"],
                email=user_data["email"],
                age=user_data["age"],
                gender=user_data["gender"],
                signup_date=date.today(),
                preferences=user_data["preferences"],
                password=user_data["password"],  # Store plaintext for reference
                hashed_password=hash_password(user_data["password"]),
            )
            db.add(test_user)

    await db.commit()

    created_count = len([u for u in test_users_data if u["user_id"] not in existing_user_ids])
    skipped_count = len([u for u in test_users_data if u["user_id"] in existing_user_ids])

    print(
        f"✅ Test users: {created_count} created, {skipped_count} already existed (loaded from config/test_users.yaml)"  # noqa: E501
    )
    print("📋 Available Test User Credentials (with rich Feast interaction history):")
    for user_data in test_users_data:
        status = "EXISTS" if user_data["user_id"] in existing_user_ids else "CREATED"
        desc = user_data.get("description", "N/A")
        print(
            f"   [{status}] {user_data['email']} | Password: {user_data['password']} "
            f"| Feast ID: {user_data['user_id']} | {desc}"
        )


if __name__ == "__main__":
    asyncio.run(seed_users())
