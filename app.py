from flask import Flask, render_template, request, redirect, url_for, session,send_file
from werkzeug.security import check_password_hash, generate_password_hash
import mysql.connector
import os
import uuid
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
app = Flask(__name__)
app.secret_key = "campusconnect-secret-key"
load_dotenv()

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


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
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
            session["user_id"] = user["user_id"]
            session["name"] = user["full_name"]
            session["role"] = user["role"]

            return redirect(url_for("dashboard"))

        return "Invalid email or password"

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = generate_password_hash(request.form["password"])
        department = request.form["department"]
        semester = request.form["semester"]
        role = request.form["role"]

        cursor = db.cursor()

        query = """
            INSERT INTO users
            (full_name, email, password, role, department, semester)
            VALUES (%s, %s, %s, %s, %s, %s)
        """

        values = (
            name,
            email,
            password,
            role,
            department,
            semester
        )

        cursor.execute(query, values)
        db.commit()
        cursor.close()

        return redirect(url_for("login"))

    return render_template("register.html")
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

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

    upload_folder = "static/uploads"
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

    if not resource:
        return "Resource not found"

    return send_file(
        resource["file_path"],
        as_attachment=True,
        download_name=resource["file_name"]
    )
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

    upload_folder = "static/uploads"
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

    if not paper:
        return "Question paper not found"

    return send_file(
        paper["file_path"],
        as_attachment=True,
        download_name=paper["file_name"]
    )

if __name__ == "__main__":
    app.run(debug=True)