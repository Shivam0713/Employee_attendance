"""Create the first Northstar administrator without putting credentials in source."""
import getpass
import os
import re
from datetime import datetime, timezone

from argon2 import PasswordHasher
from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient


def main():
    load_dotenv()
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise SystemExit("MONGO_URI is missing. Configure it in .env first.")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[os.getenv("MONGO_DB", "attendance_db")]
    users = db.auth_users
    users.create_index([("email", ASCENDING)], unique=True, name="uq_auth_email")
    existing_admins = list(users.find({"role": "admin", "active": True}, {"email": 1, "_id": 0}).sort("email", 1))
    if existing_admins:
        print("Existing administrator email(s):")
        for account in existing_admins:
            print(f"  {account['email']}")
        raise SystemExit("Bootstrap only creates the first admin. Use one of these email addresses to sign in.")
    email = input("Admin email: ").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise SystemExit("Enter a valid email address.")
    password = getpass.getpass("Admin password (minimum 8 characters): ")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords did not match.")
    try:
        users.insert_one({"email": email, "name": "Administrator", "password_hash": PasswordHasher().hash(password), "role": "admin", "active": True, "created_at": datetime.now(timezone.utc).replace(microsecond=0)})
    except Exception as exc:
        raise SystemExit(f"Admin account was not created: {type(exc).__name__}")
    finally:
        client.close()
    print(f"Created first administrator account for {email}.")


if __name__ == "__main__":
    main()
