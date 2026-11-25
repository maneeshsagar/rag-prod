"""
Create a login (or reset its password).

Run with:  make user EMAIL=you@example.com NAME="Your Name" PASSWORD=secret [ROLE=admin]
"""

import argparse

from sqlalchemy import select

from app.core.auth import hash_password
from app.db.models import User
from app.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--role", default="user", choices=["user", "admin"])
    args = parser.parse_args()

    email = args.email.strip().lower()
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, name=args.name, password_hash="")
            session.add(user)
        user.name, user.role = args.name, args.role
        user.password_hash = hash_password(args.password)
        session.commit()
        print(f"User ready: {email} ({args.role})")


if __name__ == "__main__":
    main()
