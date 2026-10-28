from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from werkzeug.security import check_password_hash, generate_password_hash
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

if __name__ == "__main__":
    app.run(debug=True)