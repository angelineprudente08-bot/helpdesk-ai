"""Create the first administrator account (run once, from the command line)."""
from getpass import getpass
from werkzeug.security import generate_password_hash
from db import get_db

name = input("Admin name: ").strip()
email = input("Admin email: ").strip().lower()
password = getpass("Password (at least 12 characters, hidden while typing): ")
if len(password) < 12:
    raise SystemExit("Password must be at least 12 characters long.")

conn = get_db()
if conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
    conn.execute("UPDATE users SET role = 'administrator' WHERE email = ?", (email,))
    print("That email already had an account. It is now an administrator.")
else:
    conn.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, 'administrator')",
                 (name, email, generate_password_hash(password)))
    print("Administrator account created.")
conn.commit()
conn.close()