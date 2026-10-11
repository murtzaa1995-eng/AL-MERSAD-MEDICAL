import os
import secrets
from functools import wraps

from flask import abort, current_app, g, redirect, request, session, url_for
from werkzeug.security import generate_password_hash

from . import db
from .services import now

PRIVATE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "pdf", "dcm", "docx"}
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
HOME = {"admin": "admin.dashboard", "hospital": "hospital.dashboard", "doctor": "doctor.dashboard"}


def current_user():
    if "user" not in g:
        uid = session.get("uid")
        g.user = db.q1("SELECT * FROM users WHERE id=? AND active=1", (uid,)) if uid else None
    return g.user


def login_required(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = current_user()
            if not u:
                return redirect(url_for("public.login", next=request.path))
            if roles and u["role"] not in roles:
                abort(403)
            return f(*a, **k)
        return w
    return deco


def hospital_of_user():
    h = db.q1("SELECT h.*, u.name, u.phone, u.email FROM hospitals h JOIN users u ON u.id=h.user_id WHERE h.user_id=?",
              (current_user()["id"],))
    if not h:
        abort(403)
    return h


def doctor_of_user():
    d = db.q1("SELECT d.*, u.name, u.phone, u.email FROM doctors d JOIN users u ON u.id=d.user_id WHERE d.user_id=?",
              (current_user()["id"],))
    if not d:
        abort(403)
    return d


def audit(action, entity=None, entity_id=None, detail=None):
    u = current_user()
    db.ex("INSERT INTO audit_log(user_id,role,action,entity,entity_id,detail,ip,created_at) VALUES (?,?,?,?,?,?,?,?)",
          (u["id"] if u else None, u["role"] if u else "patient", action, entity, entity_id, detail,
           request.remote_addr, now()))


def create_user(role, name, email, phone, password, active=1, commit=True):
    return db.ex("INSERT INTO users(role,name,email,phone,password_hash,active,created_at) VALUES (?,?,?,?,?,?,?)",
                 (role, name, email.strip().lower(), phone, generate_password_hash(password), active, now()), commit=commit)


def ext_of(filename):
    return filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""


def bad_files(files, allowed=PRIVATE_EXT):
    return [f.filename for f in files if f and f.filename and ext_of(f.filename) not in allowed]


def _store(fs, subdir):
    ext = ext_of(fs.filename)
    folder = os.path.join(current_app.config["UPLOAD_DIR"], subdir)
    os.makedirs(folder, exist_ok=True)
    name = secrets.token_hex(16) + "." + ext
    path = os.path.join(folder, name)
    fs.save(path)
    return name, os.path.getsize(path)


def attach_files(order_id, kind, files, user_id=None):
    """يحفظ ملفات طبية خاصة (لا تُخدَّم إلا عبر /files/<id> بعد فحص الصلاحية)."""
    n = 0
    for fs in files:
        if not fs or not fs.filename:
            continue
        name, size = _store(fs, "private")
        db.ex("INSERT INTO files(order_id,kind,stored_name,original_name,size,uploaded_by,created_at) VALUES (?,?,?,?,?,?,?)",
              (order_id, kind, name, os.path.basename(fs.filename)[:120], size, user_id, now()))
        n += 1
    return n


def save_branding(fs):
    """شعار/ختم/توقيع: أسماء عشوائية تُخدَّم عبر /media/<name>."""
    if not fs or not fs.filename:
        return None
    if ext_of(fs.filename) not in IMAGE_EXT:
        raise ValueError("يجب أن تكون الصورة PNG أو JPG أو WEBP")
    return _store(fs, "branding")[0]
