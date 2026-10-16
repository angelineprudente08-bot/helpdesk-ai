import csv
import io
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db
from prediction import predict, MIN_CONFIDENCE
from auto_response import build_acknowledgment

app = Flask(__name__)
app.secret_key = "change-this-secret-key"   # signs the session cookie so nobody can fake a login


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if len(password) < 12:
            flash("Password must be at least 12 characters long.", "danger")
            return render_template("register.html")

        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.close()
            flash("That email is already registered.", "danger")
            return render_template("register.html")

        conn.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, 'requester')",
            (name, email, generate_password_hash(password)),
        )
        conn.commit()
        conn.close()
        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        # same message for "no such email" and "wrong password", so attackers can't tell which
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Wrong email or password.", "danger")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user["id"]      # remember who is logged in
        session["name"] = user["name"]
        session["role"] = user["role"]
        flash(f"Welcome, {user['name']}!", "success")
        return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()                          # forget the user
    flash("You have logged out.", "info")
    return redirect(url_for("home"))


def login_required(view):
    """Put @login_required above a page to make it members-only."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


def role_required(*roles):
    """Like login_required, but also checks the user's role."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if session.get("role") not in roles:
                flash("You do not have access to that page.", "danger")
                return redirect(url_for("home"))
            return view(*args, **kwargs)
        return login_required(wrapper)
    return decorator


@app.route("/tickets/new", methods=["GET", "POST"])
@login_required
def new_ticket():
    if request.method == "POST":
        title = request.form["title"].strip()
        description = request.form["description"].strip()

        prediction = predict(title + "\n" + description)
        # apply a suggestion only when the model is confident enough; otherwise a technician decides
        category = prediction["category"] if (prediction["category_score"] or 0) >= MIN_CONFIDENCE else None
        priority = prediction["priority"] if (prediction["priority_score"] or 0) >= MIN_CONFIDENCE else None

        conn = get_db()
        cur = conn.execute(
            "INSERT INTO tickets (title, description, requester_id, category, priority) VALUES (?, ?, ?, ?, ?)",
            (title, description, session["user_id"], category, priority),
        )
        ticket_id = cur.lastrowid
        conn.execute(   # keep the model's original guess, even if it was not applied
            "INSERT INTO predictions (ticket_id, category, priority, category_score, priority_score, model_version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticket_id, prediction["category"], prediction["priority"],
             prediction["category_score"], prediction["priority_score"], prediction["model_version"]),
        )
        conn.execute(
            "INSERT INTO history (ticket_id, actor_id, detail) VALUES (?, ?, ?)",
            (ticket_id, session["user_id"], "Ticket created"),
        )
        conn.execute(   # automatic acknowledgment, shown in the ticket's conversation
            "INSERT INTO messages (ticket_id, author_id, body, is_automated) VALUES (?, ?, ?, 1)",
            (ticket_id, session["user_id"], build_acknowledgment(ticket_id, category, priority)),
        )
        conn.commit()
        conn.close()
        flash(f"Ticket #{ticket_id} submitted.", "success")
        return redirect(url_for("my_tickets"))

    return render_template("new_ticket.html")


@app.route("/tickets")
@login_required
def my_tickets():
    conn = get_db()
    tickets = conn.execute(
        "SELECT id, title, category, priority, status, created_at FROM tickets WHERE requester_id = ? ORDER BY id DESC",
        (session["user_id"],),
    ).fetchall()
    conn.close()
    return render_template("my_tickets.html", tickets=tickets)


@app.route("/queue")
@role_required("technician", "administrator")
def queue():
    conn = get_db()
    tickets = conn.execute("""
        SELECT t.id, t.title, t.category, t.priority, t.status,
               u.name AS requester,
               p.category AS suggested_category, p.category_score,
               p.priority AS suggested_priority, p.priority_score
        FROM tickets t
        JOIN users u ON u.id = t.requester_id
                LEFT JOIN predictions p ON p.ticket_id = t.id
        WHERE ? = 'administrator' OR t.technician_id = ?
        ORDER BY CASE t.priority WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                                 WHEN 'Medium' THEN 3 WHEN 'Low' THEN 4 ELSE 0 END,
                 t.created_at
    """, (session["role"], session["user_id"])).fetchall()
    conn.close()
    return render_template("queue.html", tickets=tickets)


PRIORITIES = ["Low", "Medium", "High", "Critical"]

# which status a ticket may move to next (a ticket leaves "New" only by being assigned)
TRANSITIONS = {
    "New": [],
    "Assigned": ["In Progress"],
    "In Progress": ["Waiting for User", "Resolved"],
    "Waiting for User": ["In Progress", "Resolved"],
    "Resolved": ["In Progress", "Closed"],
    "Closed": [],
}


def log(conn, ticket_id, detail):
    """Write one line to the history table."""
    conn.execute("INSERT INTO history (ticket_id, actor_id, detail) VALUES (?, ?, ?)",
                 (ticket_id, session["user_id"], detail))


@app.route("/tickets/<int:ticket_id>", methods=["GET", "POST"])
@login_required
def ticket_detail(ticket_id):
    conn = get_db()
    ticket = conn.execute("""
        SELECT t.*, u.name AS requester, tech.name AS technician
        FROM tickets t
        JOIN users u ON u.id = t.requester_id
        LEFT JOIN users tech ON tech.id = t.technician_id
        WHERE t.id = ?""", (ticket_id,)).fetchone()
    is_staff = session["role"] in ("technician", "administrator")

        # requesters see only their own tickets, technicians only the tickets assigned to them
    role = session["role"]
    not_allowed = (ticket is None
                   or (role == "requester" and ticket["requester_id"] != session["user_id"])
                   or (role == "technician" and ticket["technician_id"] != session["user_id"]))
    if not_allowed:
        conn.close()
        flash("Ticket not found.", "danger")
        return redirect(url_for("home"))

    # anyone who can see the ticket may write a message
    if request.method == "POST" and request.form.get("action") == "message":
        body = request.form["body"].strip()
        if ticket["status"] == "Closed":
            flash("This ticket is closed.", "danger")
        elif body:
            conn.execute("INSERT INTO messages (ticket_id, author_id, body) VALUES (?, ?, ?)",
                         (ticket_id, session["user_id"], body))
            # when the requester answers, the ticket goes back to the technician
            if not is_staff and ticket["status"] == "Waiting for User":
                conn.execute("UPDATE tickets SET status = 'In Progress' WHERE id = ?", (ticket_id,))
                log(conn, ticket_id, "Status Waiting for User -> In Progress (requester replied)")
        conn.commit()
        conn.close()
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    if request.method == "POST" and is_staff:
        action = request.form["action"]

        if action == "assign" and session["role"] != "administrator":
            flash("Only an administrator can assign tickets.", "danger")

        elif action == "assign":
            tech = conn.execute("SELECT id, name FROM users WHERE id = ? AND role = 'technician'",
                                (request.form["technician_id"],)).fetchone()
            if tech is None:
                flash("Choose a technician.", "danger")
            elif ticket["status"] in ("Resolved", "Closed"):
                flash("Reopen the ticket before reassigning it.", "danger")
            else:
                new_status = "Assigned" if ticket["status"] == "New" else ticket["status"]
                conn.execute("UPDATE tickets SET technician_id = ?, status = ? WHERE id = ?",
                             (tech["id"], new_status, ticket_id))
                log(conn, ticket_id, f"Assigned to {tech['name']}; status {new_status}")
                flash("Ticket assigned.", "success")

        elif action == "classify":
            category = request.form["category"]
            priority = request.form["priority"]
            known = conn.execute("SELECT 1 FROM categories WHERE name = ?", (category,)).fetchone()
            if not known or priority not in PRIORITIES:
                flash("Choose a valid category and priority.", "danger")
            else:
                conn.execute("UPDATE tickets SET category = ?, priority = ? WHERE id = ?",
                             (category, priority, ticket_id))
                old = f"{ticket['category'] or 'none'}/{ticket['priority'] or 'none'}"
                log(conn, ticket_id, f"Classification reviewed: {old} -> {category}/{priority}")
                flash("Classification saved.", "success")

        elif action == "status":
            new_status = request.form["status"]
            resolution = request.form.get("resolution", "").strip()
            if new_status not in TRANSITIONS[ticket["status"]]:
                flash("That status change is not allowed.", "danger")
            elif new_status == "Resolved" and not resolution:
                flash("Write the solution before resolving the ticket.", "danger")
            else:
                if new_status == "Resolved":
                    conn.execute("UPDATE tickets SET status = ?, resolution = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                                 (new_status, resolution, ticket_id))
                elif ticket["status"] == "Resolved" and new_status == "In Progress":   # reopened
                    conn.execute("UPDATE tickets SET status = ?, resolved_at = NULL WHERE id = ?",
                                 (new_status, ticket_id))
                else:
                    conn.execute("UPDATE tickets SET status = ? WHERE id = ?", (new_status, ticket_id))
                log(conn, ticket_id, f"Status {ticket['status']} -> {new_status}")
                flash(f"Status changed to {new_status}.", "success")

        conn.commit()
        conn.close()
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    prediction = conn.execute("SELECT * FROM predictions WHERE ticket_id = ?", (ticket_id,)).fetchone()
    history = conn.execute("""
        SELECT h.detail, h.created_at, u.name FROM history h
        JOIN users u ON u.id = h.actor_id
        WHERE h.ticket_id = ? ORDER BY h.id""", (ticket_id,)).fetchall()
    messages = conn.execute("""
        SELECT m.body, m.created_at, m.is_automated, u.name, u.role FROM messages m
        JOIN users u ON u.id = m.author_id
        WHERE m.ticket_id = ? ORDER BY m.id""", (ticket_id,)).fetchall()
    technicians = conn.execute("SELECT id, name FROM users WHERE role = 'technician' ORDER BY name").fetchall()
    categories = conn.execute("SELECT name FROM categories ORDER BY name").fetchall()
    # recommended team: use the confirmed category, or the model's guess if there is none yet
    team_category = ticket["category"] or (prediction["category"] if prediction else None)
    team = conn.execute("SELECT team FROM categories WHERE name = ?", (team_category,)).fetchone()
    conn.close()
    return render_template("ticket.html", ticket=ticket, prediction=prediction, history=history,
                           messages=messages, technicians=technicians, categories=categories,
                           priorities=PRIORITIES, next_statuses=TRANSITIONS[ticket["status"]],
                           is_staff=is_staff, team=team["team"] if team else None)
ROLES = ["requester", "technician", "administrator"]


@app.route("/admin/users", methods=["GET", "POST"])
@role_required("administrator")
def admin_users():
    conn = get_db()
    if request.method == "POST":
        user_id = int(request.form["user_id"])
        role = request.form["role"]
        if role not in ROLES:
            flash("Unknown role.", "danger")
        elif user_id == session["user_id"]:
            flash("You cannot change your own role.", "warning")
        else:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            conn.commit()
            flash("Role updated.", "success")
        conn.close()
        return redirect(url_for("admin_users"))

    users = conn.execute("SELECT id, name, email, role FROM users ORDER BY id").fetchall()
    conn.close()
    return render_template("admin_users.html", users=users, roles=ROLES)
@app.route("/admin/categories", methods=["GET", "POST"])
@role_required("administrator")
def admin_categories():
    conn = get_db()
    if request.method == "POST":
        name = request.form["name"].strip()
        team = request.form["team"].strip()
        if not name or not team:
            flash("Category and team are both required.", "danger")
        elif request.form.get("action") == "add":
            if conn.execute("SELECT 1 FROM categories WHERE name = ?", (name,)).fetchone():
                flash("That category already exists.", "warning")
            else:
                conn.execute("INSERT INTO categories (name, team) VALUES (?, ?)", (name, team))
                conn.commit()
                flash(f"Category {name} added.", "success")
        else:
            conn.execute("UPDATE categories SET team = ? WHERE name = ?", (team, name))
            conn.commit()
            flash(f"Team for {name} updated.", "success")
        conn.close()
        return redirect(url_for("admin_categories"))

    categories = conn.execute("SELECT name, team FROM categories ORDER BY name").fetchall()
    conn.close()
    return render_template("admin_categories.html", categories=categories)
STATUSES = ["New", "Assigned", "In Progress", "Waiting for User", "Resolved", "Closed"]


@app.route("/admin/reports")
@role_required("administrator")
def admin_reports():
    conn = get_db()
    counts = {row["status"]: row["n"] for row in conn.execute(
        "SELECT status, COUNT(*) AS n FROM tickets GROUP BY status")}
    avg_hours = conn.execute("""
        SELECT AVG((julianday(resolved_at) - julianday(created_at)) * 24) FROM tickets
        WHERE resolved_at IS NOT NULL""").fetchone()[0]
    # how often the technician kept the model's suggestion (only tickets a technician reviewed)
    agree = conn.execute("""
        SELECT COUNT(*) AS reviewed,
               SUM(t.category = p.category) AS same_category,
               SUM(t.priority = p.priority) AS same_priority
        FROM tickets t JOIN predictions p ON p.ticket_id = t.id
        WHERE EXISTS (SELECT 1 FROM history h WHERE h.ticket_id = t.id
                      AND h.detail LIKE 'Classification reviewed%')""").fetchone()
    conn.close()
    return render_template("admin_reports.html", statuses=STATUSES, counts=counts,
                           total=sum(counts.values()), avg_hours=avg_hours, agree=agree)


@app.route("/admin/reports.csv")
@role_required("administrator")
def admin_reports_csv():
    conn = get_db()
    rows = conn.execute("""
        SELECT t.id, t.title, u.name AS requester, tech.name AS technician,
               t.category, t.priority, t.status, t.created_at, t.resolved_at,
               p.category AS predicted_category, p.priority AS predicted_priority
        FROM tickets t
        JOIN users u ON u.id = t.requester_id
        LEFT JOIN users tech ON tech.id = t.technician_id
        LEFT JOIN predictions p ON p.ticket_id = t.id
        ORDER BY t.id""").fetchall()
    conn.close()

    def safe(value):
        # Excel runs cells that start with = + - @ as formulas, so put a ' in front of them
        text = "" if value is None else str(value)
        return "'" + text if text[:1] in ("=", "+", "-", "@") else text

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(rows[0].keys() if rows else ["id"])
    for row in rows:
        writer.writerow([safe(value) for value in row])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=helpdesk_report.csv"})
@app.after_request
def add_security_headers(response):
    # stop other websites from showing our pages inside a frame (clickjacking)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    # stop the browser from guessing file types
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

if __name__ == "__main__":
    app.run(debug=True)