from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash
)
import sqlite3
from datetime import date,timedelta
import calendar
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)

# Change this secret key before deploying
app.secret_key = "taskflow-development-secret-key-change-before-deployment"


# Database connection
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


# Create database tables
def create_table():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'Pending',
            priority TEXT DEFAULT 'Medium',
            due_date TEXT
        )
    """)

    columns = conn.execute(
        "PRAGMA table_info(tasks)"
    ).fetchall()

    column_names = [column["name"] for column in columns]

    if "priority" not in column_names:
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'Medium'"
        )

    if "due_date" not in column_names:
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN due_date TEXT"
        )

    if "user_id" not in column_names:
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN user_id INTEGER"
        )

    conn.commit()
    conn.close()


# Login protection
def login_required(route_function):
    @wraps(route_function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "error")
            return redirect(url_for("login"))

        return route_function(*args, **kwargs)

    return wrapper


# Home page
@app.route("/")
def home():
    return render_template("index.html")


# User registration
@app.route("/register", methods=["GET", "POST"])
def register():

    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not email or not password:
            flash("Please fill in all fields.", "error")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return redirect(url_for("register"))

        conn = get_db()

        existing_user = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if existing_user:
            conn.close()
            flash("Email already registered. Please login.", "error")
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)

        cursor = conn.execute(
            """
            INSERT INTO users (username, email, password)
            VALUES (?, ?, ?)
            """,
            (username, email, hashed_password)
        )

        new_user_id = cursor.lastrowid

        # Assign old unowned tasks to the first account
        conn.execute(
            """
            UPDATE tasks
            SET user_id = ?
            WHERE user_id IS NULL
            AND NOT EXISTS (
                SELECT 1 FROM users WHERE id < ?
            )
            """,
            (new_user_id, new_user_id)
        )

        conn.commit()
        conn.close()

        flash("Registration successful! Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


# User login
@app.route("/login", methods=["GET", "POST"])
def login():

    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()

        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        conn.close()

        if user and check_password_hash(user["password"], password):

            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]

            flash("Login successful!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")


# Logout
@app.route("/logout", methods=["POST"])
@login_required
def logout():

    session.clear()
    flash("You have logged out successfully.", "success")

    return redirect(url_for("login"))


# Dashboard with Search, Filter, Sorting and Notifications
@app.route("/dashboard")
@login_required
def dashboard():

    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "All")
    sort = request.args.get("sort", "newest")

    today = date.today().isoformat()
    user_id = session["user_id"]

    conn = get_db()

    # Get tasks with due status
    query = """
        SELECT *,
        CASE
            WHEN status = 'Completed' THEN 'Completed'
            WHEN due_date IS NULL OR due_date = '' THEN 'No Due Date'
            WHEN due_date < ? THEN 'Overdue'
            WHEN due_date = ? THEN 'Due Today'
            ELSE 'Upcoming'
        END AS due_status
        FROM tasks
        WHERE user_id = ?
    """

    params = [today, today, user_id]

    # Search
    if search:
        query += " AND title LIKE ?"
        params.append("%" + search + "%")

    # Status filter
    if status_filter != "All":
        query += " AND status = ?"
        params.append(status_filter)

    # Sorting
    if sort == "oldest":
        query += " ORDER BY id ASC"

    elif sort == "due_date":
        query += """
            ORDER BY
            CASE
                WHEN due_date IS NULL OR due_date = '' THEN 1
                ELSE 0
            END,
            due_date ASC,
            id DESC
        """

    else:
        sort = "newest"
        query += " ORDER BY id DESC"

    tasks = conn.execute(query, params).fetchall()

    # Total tasks
    total = conn.execute(
        """
        SELECT COUNT(*) FROM tasks
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()[0]

    # Pending tasks
    pending = conn.execute(
        """
        SELECT COUNT(*) FROM tasks
        WHERE user_id = ? AND status = 'Pending'
        """,
        (user_id,)
    ).fetchone()[0]

    # Completed tasks
    completed = conn.execute(
        """
        SELECT COUNT(*) FROM tasks
        WHERE user_id = ? AND status = 'Completed'
        """,
        (user_id,)
    ).fetchone()[0]

    # Task notifications
    notifications = conn.execute(
        """
        SELECT
            SUM(
                CASE
                    WHEN due_date < ? AND due_date != ''
                    THEN 1 ELSE 0
                END
            ) AS overdue,

            SUM(
                CASE
                    WHEN due_date = ?
                    THEN 1 ELSE 0
                END
            ) AS due_today,

            SUM(
                CASE
                    WHEN due_date > ?
                    AND due_date <= date(?, '+7 days')
                    THEN 1 ELSE 0
                END
            ) AS upcoming

        FROM tasks
        WHERE user_id = ?
        AND status != 'Completed'
        """,
        (today, today, today, today, user_id)
    ).fetchone()

    # Notification counts
    overdue_count = notifications["overdue"] or 0
    due_today_count = notifications["due_today"] or 0
    upcoming_count = notifications["upcoming"] or 0

    conn.close()

    # Send all dashboard information to HTML
    return render_template(
        "dashboard.html",
        tasks=tasks,
        total=total,
        pending=pending,
        completed=completed,
        search=search,
        status_filter=status_filter,
        today=today,
        sort=sort,
        username=session.get("username"),
        overdue_count=overdue_count,
        due_today_count=due_today_count,
        upcoming_count=upcoming_count
    )

# Calendar View
@app.route("/calendar")
@login_required
def calendar_view():

    today = date.today()

    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    # Validate month and year
    if month < 1 or month > 12 or year < 1900 or year > 9999:
        year = today.year
        month = today.month

    cal = calendar.Calendar(firstweekday=6)

    month_days = cal.monthdayscalendar(year, month)

    conn = get_db()

    tasks = conn.execute(
        """
        SELECT id, title, status, priority, due_date
        FROM tasks
        WHERE user_id = ?
        AND due_date IS NOT NULL
        AND due_date != ''
        """,
        (session["user_id"],)
    ).fetchall()

    conn.close()

    # Group tasks by due date
    tasks_by_date = {}

    for task in tasks:
        task_date = task["due_date"]

        tasks_by_date.setdefault(task_date, []).append(task)

    # Previous month
    if month == 1:
        prev_month = 12
        prev_year = year - 1
    else:
        prev_month = month - 1
        prev_year = year

    # Next month
    if month == 12:
        next_month = 1
        next_year = year + 1
    else:
        next_month = month + 1
        next_year = year

    return render_template(
        "calendar.html",
        month_days=month_days,
        tasks_by_date=tasks_by_date,
        year=year,
        month=month,
        month_name=calendar.month_name[month],
        prev_month=prev_month,
        prev_year=prev_year,
        next_month=next_month,
        next_year=next_year,
        today=today.isoformat()
    )
# Add task
@app.route("/add", methods=["GET", "POST"])
@login_required
def add_task():

    if request.method == "POST":

        title = request.form.get("title", "").strip()
        description = request.form.get("description", "")
        status = request.form.get("status", "Pending")
        priority = request.form.get("priority", "Medium")
        due_date = request.form.get("due_date", "")

        if title:

            conn = get_db()

            conn.execute(
                """
                INSERT INTO tasks
                (title, description, status, priority, due_date, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    description,
                    status,
                    priority,
                    due_date,
                    session["user_id"]
                )
            )

            conn.commit()
            conn.close()

            return redirect(url_for("dashboard"))

        flash("Task title is required.", "error")

    return render_template("task_form.html")


# Mark task as completed
@app.route("/complete/<int:task_id>")
@login_required
def complete_task(task_id):

    conn = get_db()

    conn.execute(
        """
        UPDATE tasks
        SET status = 'Completed'
        WHERE id = ? AND user_id = ?
        """,
        (task_id, session["user_id"])
    )

    conn.commit()
    conn.close()

    return redirect(url_for("dashboard"))


# Delete task
@app.route("/delete/<int:task_id>")
@login_required
def delete_task(task_id):

    conn = get_db()

    conn.execute(
        """
        DELETE FROM tasks
        WHERE id = ? AND user_id = ?
        """,
        (task_id, session["user_id"])
    )

    conn.commit()
    conn.close()

    return redirect(url_for("dashboard"))


# Edit task
@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
@login_required
def edit_task(task_id):

    conn = get_db()

    task = conn.execute(
        """
        SELECT * FROM tasks
        WHERE id = ? AND user_id = ?
        """,
        (task_id, session["user_id"])
    ).fetchone()

    if task is None:
        conn.close()
        return "Task not found", 404

    if request.method == "POST":

        title = request.form.get("title", "").strip()
        description = request.form.get("description", "")
        status = request.form.get("status", "Pending")
        priority = request.form.get("priority", "Medium")
        due_date = request.form.get("due_date", "")

        if title:

            conn.execute(
                """
                UPDATE tasks
                SET title = ?, description = ?, status = ?,
                    priority = ?, due_date = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    title,
                    description,
                    status,
                    priority,
                    due_date,
                    task_id,
                    session["user_id"]
                )
            )

            conn.commit()
            conn.close()

            return redirect(url_for("dashboard"))

        flash("Task title is required.", "error")

    conn.close()

    return render_template("edit_task.html", task=task)


# Run application
if __name__ == "__main__":
    create_table()
    app.run(debug=True, use_reloader=False)