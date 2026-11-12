from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from werkzeug.security import check_password_hash, generate_password_hash
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import smtplib
from email.message import EmailMessage
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import mysql.connector
import os
import uuid
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("SECRET_KEY is missing. Add it to your .env file.")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
csrf = CSRFProtect(app)


@app.errorhandler(CSRFError)
def csrf_error(e):
    flash("Your session expired. Please try again.", "error")
    return redirect(url_for("login"))
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")


@app.errorhandler(429)
def too_many_requests(e):
    flash("Too many attempts. Please wait a minute and try again.", "error")
    return redirect(url_for("login"))

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx"}
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )

# MySQL Database Connection
db = mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME")
)
@app.before_request
def keep_db_alive():
    db.ping(reconnect=True, attempts=3, delay=1)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        cursor = db.cursor(dictionary=True)

        query = "SELECT * FROM users WHERE email = %s"
        cursor.execute(query, (email,))

        user = cursor.fetchone()
        cursor.close()

    
        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["user_id"]
            session["name"] = user["full_name"]
            session["role"] = user["role"]
            session["show_welcome"] = True

            return redirect(url_for("dashboard"))
        
        flash("Incorrect email or password. Please try again.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")



@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = generate_password_hash(request.form["password"])
        department = request.form["department"]
        semester = request.form["semester"]
        role = request.form["role"]

        cursor = db.cursor()

        cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            cursor.close()
            flash("An account with this email already exists. Please log in.", "error")
            return redirect(url_for("register"))

        cursor.execute(
            """
            INSERT INTO users
            (full_name, email, password, role, department, semester)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (name, email, password, role, department, semester)
        )
        db.commit()
        cursor.close()

        flash("Account created. You can log in now.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")
@app.route("/skillswap")
def skillswap():
    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template("skillswap.html")

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS n FROM resources")
    resource_count = cursor.fetchone()["n"]

    cursor.execute("SELECT COUNT(*) AS n FROM question_papers")
    paper_count = cursor.fetchone()["n"]

    cursor.execute("SELECT COUNT(*) AS n FROM users")
    user_count = cursor.fetchone()["n"]

    cursor.execute("""
        SELECT resource_id AS item_id, title, subject, uploaded_at, 'resource' AS kind
        FROM resources
        UNION ALL
        SELECT paper_id, title, subject, uploaded_at, 'paper'
        FROM question_papers
        ORDER BY uploaded_at DESC
        LIMIT 5
    """)
    recent = cursor.fetchall()
    cursor.close()

    return render_template(
        "dashboard.html",
        resource_count=resource_count,
        paper_count=paper_count,
        user_count=user_count,
        recent=recent
    )


@app.route("/dismiss-welcome", methods=["POST"])
def dismiss_welcome():
    session.pop("show_welcome", None)
    return "", 204

@app.route("/resources")
def resources():
    if "user_id" not in session:
        return redirect(url_for("login"))

    cursor = db.cursor(dictionary=True)

    # Fetch study resources
    query = """
        SELECT
            r.resource_id,
            r.title,
            r.subject,
            r.semester,
            r.resource_type,
            r.file_name,
            r.file_path,
            u.full_name AS uploaded_by,
            r.uploaded_at
        FROM resources r
        LEFT JOIN users u
            ON r.uploaded_by = u.user_id
        ORDER BY r.uploaded_at DESC
    """

    cursor.execute(query)
    resources = cursor.fetchall()

    # Fetch question papers
    query = """
        SELECT
            q.paper_id,
            q.title,
            q.department,
            q.subject,
            q.semester,
            q.exam_type,
            q.exam_year,
            q.file_name,
            q.file_path,
            u.full_name AS uploaded_by,
            q.uploaded_at
        FROM question_papers q
        LEFT JOIN users u
            ON q.uploaded_by = u.user_id
        ORDER BY q.exam_year DESC, q.uploaded_at DESC
    """

    cursor.execute(query)
    question_papers = cursor.fetchall()

    cursor.close()

    return render_template(
        "resources.html",
        resources=resources,
        question_papers=question_papers
    )
@app.route("/upload-resource", methods=["POST"])
def upload_resource():

    if "user_id" not in session:
        return redirect(url_for("login"))

    title = request.form["title"]
    subject = request.form["subject"]
    semester = request.form["semester"]
    resource_type = request.form["resource_type"]

    file = request.files["file"]

    if file.filename == "":
        return "Please select a file"
    if not allowed_file(file.filename):
        return "Invalid file type. Only PDF, DOC and DOCX files are allowed."  

    upload_folder = "uploads"
    os.makedirs(upload_folder, exist_ok=True)

    original_filename = secure_filename(file.filename)
    filename = f"{uuid.uuid4().hex}_{original_filename}"

    file_path = os.path.join(upload_folder, filename)
    file.save(file_path)

    cursor = db.cursor()

    query = """
        INSERT INTO resources
        (title, subject, semester, resource_type,
         file_name, file_path, uploaded_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """

    values = (
    title,
    subject,
    semester,
    resource_type,
    original_filename,
    file_path,
    session["user_id"]
)

    cursor.execute(query, values)
    db.commit()

    cursor.close()
    flash("Resource uploaded. Thank you for sharing!", "success")
    return redirect(url_for("resources"))
@app.route("/download-resource/<int:resource_id>")
def download_resource(resource_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    cursor = db.cursor(dictionary=True)

    query = """
        SELECT file_path, file_name
        FROM resources
        WHERE resource_id = %s
    """

    cursor.execute(query, (resource_id,))
    resource = cursor.fetchone()

    cursor.close()

    if not resource or not resource["file_path"] or not os.path.exists(resource["file_path"]):
        flash("This file is not available.", "error")
        return redirect(url_for("resources"))

    return send_file(
        resource["file_path"],
        as_attachment=True,
        download_name=resource["file_name"]
    )
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/upload-question-paper", methods=["POST"])
def upload_question_paper():
    if "user_id" not in session:
        return redirect(url_for("login"))

    title = request.form["title"]
    department = request.form["department"]
    subject = request.form["subject"]
    semester = request.form["semester"]
    exam_type = request.form["exam_type"]
    exam_year = request.form["exam_year"]

    file = request.files["file"]

    if file.filename == "":
        return "Please select a file"
    if not allowed_file(file.filename):
        return "Invalid file type. Only PDF, DOC and DOCX files are allowed."

    upload_folder = "uploads"
    os.makedirs(upload_folder, exist_ok=True)

    original_filename = secure_filename(file.filename)
    filename = f"{uuid.uuid4().hex}_{original_filename}"

    file_path = os.path.join(upload_folder, filename)
    file.save(file_path)

    cursor = db.cursor()

    query = """
        INSERT INTO question_papers
        (title, department, subject, semester, exam_type,
         exam_year, file_name, file_path, uploaded_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    values = (
        title,
        department,
        subject,
        semester,
        exam_type,
        exam_year,
        original_filename,
        file_path,
        session["user_id"]
    )

    cursor.execute(query, values)
    db.commit()
    cursor.close()
    flash("Question paper uploaded. Thank you for sharing!", "success")
    return redirect(url_for("resources"))

   
@app.route("/download-question-paper/<int:paper_id>")
def download_question_paper(paper_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    cursor = db.cursor(dictionary=True)

    query = """
        SELECT file_path, file_name
        FROM question_papers
        WHERE paper_id = %s
    """

    cursor.execute(query, (paper_id,))
    paper = cursor.fetchone()
    cursor.close()

    if not paper or not paper["file_path"] or not os.path.exists(paper["file_path"]):
        flash("This file is not available.", "error")
        return redirect(url_for("resources"))

    return send_file(
        paper["file_path"],
        as_attachment=True,
        download_name=paper["file_name"]
    )
LF_CATEGORIES = ["Electronics", "Documents", "Books", "Clothing", "Accessories", "Other"]


def item_text(i):
    return f'{i["title"]} {i["title"]} {i["category"]} {i["location"]} {i["description"]}'


def find_matches(items, user_id, limit=3, threshold=0.25):
    mine = [i for i in items if i["reported_by"] == user_id]
    if not mine or len(items) < 2:
        return {}

    try:
        vectorizer = TfidfVectorizer(stop_words="english")
        matrix = vectorizer.fit_transform([item_text(i) for i in items])
    except ValueError:
        return {}

    position = {i["item_id"]: n for n, i in enumerate(items)}
    result = {}

    for m in mine:
        sims = cosine_similarity(matrix[position[m["item_id"]]], matrix)[0]
        scored = []
        for n, other in enumerate(items):
            if other["item_type"] == m["item_type"] or other["reported_by"] == user_id:
                continue
            score = sims[n] + (0.15 if other["category"] == m["category"] else 0)
            if score >= threshold:
                scored.append((round(min(score, 1) * 100), other))
        scored.sort(key=lambda s: s[0], reverse=True)
        if scored:
            result[m["item_id"]] = scored[:limit]

    return result


@app.route("/lostandfound")
def lostandfound():
    if "user_id" not in session:
        return redirect(url_for("login"))

    q = request.args.get("q", "").strip()
    item_type = request.args.get("type", "")
    category = request.args.get("category", "")

    sql = """
        SELECT i.item_id, i.item_type, i.title, i.category, i.description,
               i.location, i.event_date, i.status, i.reported_by,
               u.full_name AS reporter
        FROM lost_found_items i
        JOIN users u ON i.reported_by = u.user_id
        WHERE i.status = 'open'
    """
    params = []

    if q:
        sql += " AND (i.title LIKE %s OR i.description LIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    if item_type in ("lost", "found"):
        sql += " AND i.item_type = %s"
        params.append(item_type)
    if category in LF_CATEGORIES:
        sql += " AND i.category = %s"
        params.append(category)

    sql += " ORDER BY i.created_at DESC"

    cursor = db.cursor(dictionary=True)
    cursor.execute(sql, params)
    items = cursor.fetchall()

    cursor.execute("""
        SELECT item_id, item_type, title, category, description, location,
               event_date, reported_by, verify_question
        FROM lost_found_items
        WHERE status = 'open'
    """)
    open_items = cursor.fetchall()
    cursor.close()

    user_id = session["user_id"]
    matches = find_matches(open_items, user_id)
    my_items = [i for i in open_items if i["reported_by"] == user_id]

    return render_template(
        "lostandfound.html",
        items=items,
        categories=LF_CATEGORIES,
        q=q,
        item_type=item_type,
        category=category,
        matches=matches,
        my_items=my_items
    )


@app.route("/lostandfound/report", methods=["POST"])
def report_item():
    if "user_id" not in session:
        return redirect(url_for("login"))

    item_type = request.form["item_type"]
    title = request.form["title"].strip()
    category = request.form["category"]
    description = request.form["description"].strip()
    location = request.form["location"].strip()
    event_date = request.form["event_date"]
    verify_question = request.form.get("verify_question", "").strip() or None
    verify_answer = request.form.get("verify_answer", "").strip().lower() or None

    if (item_type not in ("lost", "found") or category not in LF_CATEGORIES
            or not title or not description or not location or not event_date):
        flash("Please fill in all the required fields.", "error")
        return redirect(url_for("lostandfound"))

    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO lost_found_items
        (item_type, title, category, description, location, event_date,
         verify_question, verify_answer, reported_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (item_type, title, category, description, location, event_date,
         verify_question, verify_answer, session["user_id"])
    )
    db.commit()
    cursor.close()

    flash("Your report has been added.", "success")
    return redirect(url_for("lostandfound"))


@app.route("/lostandfound/<int:item_id>/resolve", methods=["POST"])
def resolve_item(item_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    cursor = db.cursor()
    cursor.execute(
        "UPDATE lost_found_items SET status = 'returned' "
        "WHERE item_id = %s AND reported_by = %s",
        (item_id, session["user_id"])
    )
    db.commit()
    changed = cursor.rowcount
    cursor.close()

    if changed:
        flash("Marked as returned. Thank you!", "success")
    else:
        flash("You can only close your own reports.", "error")
    return redirect(url_for("lostandfound"))


@app.route("/lostandfound/<int:item_id>/claim", methods=["POST"])
@limiter.limit("5 per minute")
def claim_item(item_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    answer = request.form.get("answer", "").strip().lower()

    cursor = db.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT i.verify_answer, i.reported_by, i.title, u.full_name, u.email
        FROM lost_found_items i
        JOIN users u ON i.reported_by = u.user_id
        WHERE i.item_id = %s AND i.item_type = 'found' AND i.status = 'open'
        """,
        (item_id,)
    )
    item = cursor.fetchone()

    if not item or item["reported_by"] == session["user_id"]:
        cursor.close()
        flash("This item is not available to claim.", "error")
        return redirect(url_for("lostandfound"))

    if item["verify_answer"] and answer != item["verify_answer"]:
        cursor.close()
        flash("That answer does not match. Please try again.", "error")
        return redirect(url_for("lostandfound"))

    cursor.execute(
        "UPDATE lost_found_items SET status = 'claimed' WHERE item_id = %s",
        (item_id,)
    )
    db.commit()
    cursor.close()

    flash(
        f"Verified! Contact {item['full_name']} at {item['email']} "
        f"to collect your {item['title']}. Save these details now.",
        "success"
    )
    return redirect(url_for("lostandfound"))
serializer = URLSafeTimedSerializer(app.secret_key)


def send_reset_email(to_email, link):
    host = os.getenv("MAIL_HOST")
    user = os.getenv("MAIL_USER")
    password = os.getenv("MAIL_PASSWORD")

    # No email settings yet: print the link in the terminal (for testing)
    if not (host and user and password):
        print("\n[DEV] Password reset link:", link, "\n")
        return

    msg = EmailMessage()
    msg["Subject"] = "Reset your CampusConnect password"
    msg["From"] = user
    msg["To"] = to_email
    msg.set_content(
        "Open this link to reset your password (valid for 30 minutes):\n\n"
        f"{link}\n\nIf you did not ask for this, ignore this email."
    )
    with smtplib.SMTP_SSL(host, 465) as server:
        server.login(user, password)
        server.send_message(msg)


@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form["email"].strip().lower()

        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT user_id, password FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close()

        if user:
            token = serializer.dumps(
                {"uid": user["user_id"], "pw": user["password"][-10:]},
                salt="password-reset"
            )
            link = url_for("reset_password", token=token, _external=True)
            send_reset_email(email, link)

        flash("If that email is registered, a reset link has been sent.", "success")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        data = serializer.loads(token, salt="password-reset", max_age=1800)
    except (SignatureExpired, BadSignature):
        flash("This reset link is invalid or has expired.", "error")
        return redirect(url_for("forgot_password"))

    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT user_id, password FROM users WHERE user_id = %s", (data["uid"],))
    user = cursor.fetchone()
    cursor.close()

    if not user or user["password"][-10:] != data["pw"]:
        flash("This reset link was already used or is invalid.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_password = request.form["password"]
        if len(new_password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("reset_password", token=token))

        cursor = db.cursor()
        cursor.execute(
            "UPDATE users SET password = %s WHERE user_id = %s",
            (generate_password_hash(new_password), user["user_id"])
        )
        db.commit()
        cursor.close()

        flash("Password updated. You can log in now.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html")


if __name__ == "__main__":
    app.run(debug=True)