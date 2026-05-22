"""
scripts/make_password_hash.py
-----------------------------
Helper για να φτιάχνεις bcrypt hashes για να βάζεις στο secrets.toml.

Χρήση:
    python scripts/make_password_hash.py "myPassword123"
"""

import sys

import bcrypt


def main() -> None:
    if len(sys.argv) < 2:
        password = input("Password: ").strip()
    else:
        password = sys.argv[1]

    if not password:
        sys.exit("empty password")

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    print(hashed)


if __name__ == "__main__":
    main()
