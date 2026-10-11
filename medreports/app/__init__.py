import logging
import os
import secrets

from flask import Flask, abort, g, render_template, request, session

from . import db, services
from .security import HOME, create_user, current_user

log = logging.getLogger("medreports")


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


NAV = {
    "hospital": [("hospital.dashboard", "لوحة المعلومات"), ("hospital.bookings", "الحجوزات"),
                 ("hospital.booking_new", "إضافة فحص جديد"), ("hospital.exams", "فحوصاتي"),
                 ("hospital.reports", "التقارير المكتملة"), ("hospital.stats", "الإحصائيات والجرد الشهري"),
                 ("hospital.settings", "إعدادات المستشفى")],
    "doctor": [("doctor.dashboard", "لوحة المعلومات"), ("doctor.worklist", "الفحوصات الواردة"),
               ("doctor.mine", "فحوصاتي"), ("doctor.stats", "الجرد الشهري")],
    "admin": [("admin.dashboard", "الرئيسية"), ("admin.hospitals", "المستشفيات والمراكز"), ("admin.doctors", "الأطباء"),
              ("admin.exam_types", "أنواع الفحوصات والأسعار"), ("admin.orders", "كل الطلبات"),
              ("admin.finance", "الجرد المالي"), ("admin.audit_log", "سجل التدقيق")],
}

SEED_EXAMS = [
    ("xray", "الصدر", 5000), ("xray", "العمود الفقري القطني", 7000), ("xray", "الركبة", 5000),
    ("ct", "الدماغ", 20000), ("ct", "الصدر", 25000), ("ct", "البطن والحوض", 30000), ("ct", "الجيوب الأنفية", 15000),
    ("mri", "الدماغ", 40000), ("mri", "العمود الفقري القطني", 40000), ("mri", "الركبة", 35000),
    ("us", "البطن والحوض", 7000), ("us", "الغدة الدرقية", 7000),
]


def bootstrap(app):
    db.init_db()
    if db.q1("SELECT COUNT(*) c FROM exam_types")["c"] == 0:
        for m, r, p in SEED_EXAMS:
            db.ex("INSERT INTO exam_types(modality,region,base_price) VALUES (?,?,?)", (m, r, p), commit=False)
        db.commit()
    if not db.q1("SELECT 1 FROM users WHERE role='admin'"):
        email = app.config["BOOTSTRAP_ADMIN_EMAIL"]
        pw = app.config["BOOTSTRAP_ADMIN_PASSWORD"]
        generated = not pw
        pw = pw or secrets.token_urlsafe(9)
        create_user("admin", "مدير النظام", email, "", pw)
        if generated:
            print(f"\n*** تم إنشاء حساب الإدارة: {email} / {pw}  (غيّر كلمة السر فوراً) ***\n", flush=True)


def create_app(config=None):
    app = Flask(__name__)
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    data = os.environ.get("DATA_DIR", os.path.join(root, "data"))
    env = os.environ.get
    app.config.update(
        SECRET_KEY=env("SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.path.join(data, "platform.db"), UPLOAD_DIR=os.path.join(data, "uploads"),
        MAX_CONTENT_LENGTH=int(env("MAX_UPLOAD_MB", "200")) * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=env("COOKIE_SECURE", "0") == "1",
        PLATFORM_NAME=env("PLATFORM_NAME", "منصة التقارير الطبية"), CURRENCY=env("CURRENCY", "د.ع"),
        PAYMENT_PROVIDER=env("PAYMENT_PROVIDER", "mock"), SMS_PROVIDER=env("SMS_PROVIDER", "console"),
        DEV_SHOW_OTP=env("DEV_SHOW_OTP", "1") == "1", RESULT_SESSION_MINUTES=10,
        PUBLIC_BASE_URL=env("PUBLIC_BASE_URL", ""), TZ_OFFSET_HOURS=int(env("TZ_OFFSET_HOURS", "3")),
        BOOTSTRAP_ADMIN_EMAIL=env("ADMIN_EMAIL", "admin@example.com"), BOOTSTRAP_ADMIN_PASSWORD=env("ADMIN_PASSWORD", ""),
    )
    if config:
        app.config.update(config)
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    if app.config["SECRET_KEY"] == "dev-only-change-me" and not app.testing:
        log.warning("SECRET_KEY الافتراضي! عيّن SECRET_KEY قوي قبل النشر.")

    app.teardown_appcontext(db.close_db)
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["MODALITIES"] = services.MODALITIES
    app.jinja_env.filters["money"] = lambda n: f"{int(n or 0):,} {app.config['CURRENCY']}"
    app.jinja_env.filters["status"] = lambda s: services.STATUS.get(s, s)

    from . import views_admin, views_common, views_doctor, views_hospital, views_public
    for m in (views_public, views_common, views_hospital, views_doctor, views_admin):
        app.register_blueprint(m.bp)

    @app.before_request
    def _csrf():
        if request.method == "POST" and app.config.get("CSRF_ENABLED", True):
            tok = session.get("csrf")
            if not tok or not secrets.compare_digest(tok, request.form.get("csrf_token", "")):
                abort(400, "انتهت صلاحية النموذج، أعد تحميل الصفحة.")

    @app.after_request
    def _headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.endpoint and not request.endpoint.endswith("static"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def _ctx():
        u = current_user()
        unread = 0
        if u:
            unread = db.q1("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0", (u["id"],))["c"]
        return {"me": u, "nav": NAV.get(u["role"], []) if u else [], "unread": unread,
                "PLATFORM": app.config["PLATFORM_NAME"], "home": HOME.get(u["role"]) if u else "public.index"}

    for code, msg in ((400, "طلب غير صالح"), (403, "ليس لديك صلاحية"), (404, "الصفحة غير موجودة"),
                      (413, "الملف كبير جداً"), (429, "محاولات كثيرة")):
        app.register_error_handler(code, lambda e, c=code, m=msg: (render_template("error.html", code=c, msg=getattr(e, "description", m) if c == 400 else m), c))

    app.jinja_env.globals["m"] = app.jinja_env.get_template("_m.html").module
    from .cli import register
    register(app)
    with app.app_context():
        bootstrap(app)
    return app
