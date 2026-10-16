import sqlite3

DATABASE = "helpdesk.db"   # the whole database lives in this one file

def get_db():
    """Open a connection to the database."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row             # lets us use row["title"] instead of row[1]
    conn.execute("PRAGMA foreign_keys = ON")   # make SQLite enforce the REFERENCES rules
    return conn

def init_db():
    """Create all tables from schema.sql."""
    conn = get_db()
    with open("schema.sql", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    conn = get_db()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print("Tables created:", [t["name"] for t in tables])