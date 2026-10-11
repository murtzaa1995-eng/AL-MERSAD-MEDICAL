import time
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from . import db, services as sv
from .security import HOME, attach_files, audit, bad_files, current_user

bp = Blueprint("public", __name__)
_fails = {}


@bp.route("/")
def index():
    return render_template("index.html")


def _safe_next(n):
    return n if n and n.startswith("/") and not n.startswith("//") else None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        key = (request.remote_addr, email)
        cnt, last = _fails.get(key, (0, 0))
        if cnt >= 5 and time.time() - last < 600:
            flash("محاولات كثيرة، حاول بعد 10 دقائق", "error")
            return render_template("login.html"), 429
        u = db.q1("SELECT * FROM users WHERE email=?", (email,))
        if u and u["active"] and check_password_hash(u["password_hash"], request.form.get("password", "")):
            _fails.pop(key, None)
            session.clear()
            session["uid"] = u["id"]
            g.pop("user", None)
            audit("login", "user", u["id"])
            return redirect(_safe_next(request.args.get("next")) or url_for(HOME[u["role"]]))
        _fails[key] = (cnt + 1, time.time())
        flash("بيانات الدخول غير صحيحة أو الحساب موقوف", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("public.index"))


# ---------------- واجهة المريض ----------------
def _order(token):
    o = db.q1("SELECT o.*, e.modality, e.region FROM orders o LEFT JOIN exam_types e ON e.id=o.exam_type_id WHERE o.token=?", (token,))
    if not o:
        abort(404)
    return o


@bp.route("/order/new", methods=["GET", "POST"])
def order_new():
    exams = db.q("SELECT * FROM exam_types WHERE active=1 ORDER BY modality, region")
    if request.method == "POST":
        f = request.form
        name = " ".join(f.get("name", "").split())
        phone = sv.normalize_phone(f.get("phone"))
        age = f.get("age", type=int)
        exam = db.q1("SELECT * FROM exam_types WHERE id=? AND active=1", (f.get("exam_type_id", type=int),))
        sheet = request.files.get("request_sheet")
        images = [x for x in request.files.getlist("images") if x and x.filename]
        errs = []
        if len(name.split()) < 3:
            errs.append("اكتب الاسم الثلاثي")
        if age is None or not 0 <= age <= 120:
            errs.append("العمر غير صحيح")
        if f.get("sex") not in ("ذكر", "أنثى"):
            errs.append("اختر الجنس")
        if not sv.valid_phone(phone):
            errs.append("رقم الموبايل غير صحيح (مثال 07701234567)")
        if not exam:
            errs.append("اختر نوع الفحص والمنطقة")
        if not (sheet and sheet.filename):
            errs.append("ارفع ورقة طلب الفحص")
        if not images:
            errs.append("ارفع صور الفحص أو ملفاته")
        errs += [f"نوع الملف غير مسموح: {n}" for n in bad_files([sheet] + images)]
        if errs:
            for e in errs:
                flash(e, "error")
            return render_template("patient_form.html", exams=exams, f=f), 400
        token = sv.gen_token()
        oid = db.ex("""INSERT INTO orders(ref,token,source,patient_name,age,sex,phone,exam_type_id,exam_name,clinical_info,status,price,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sv.gen_ref(), token, "patient", name, age, f["sex"], phone, exam["id"], sv.exam_name(exam),
                     f.get("clinical_info", "").strip()[:2000], "pending_payment", exam["base_price"], sv.now()))
        attach_files(oid, "request_sheet", [sheet])
        attach_files(oid, "image", images)
        audit("order.create", "order", oid, "patient")
        return redirect(url_for("public.order_status", token=token))
    return render_template("patient_form.html", exams=exams, f={})


@bp.route("/order/<token>")
def order_status(token):
    o = _order(token)
    qr = None
    if o["status"] not in ("pending_payment", "paid"):
        qr = sv.qr_svg(sv.public_url("public.result", token=token))
    return render_template("patient_status.html", o=o, qr=qr)


@bp.route("/order/<token>/pay", methods=["POST"])
def order_pay(token):
    o = _order(token)
    if o["status"] != "pending_payment":
        return redirect(url_for("public.order_status", token=token))
    return redirect(sv.start_checkout(o))


@bp.route("/pay/mock/<token>")
def mock_pay(token):
    if current_app.config["PAYMENT_PROVIDER"] != "mock":
        abort(404)
    return render_template("mock_pay.html", o=_order(token))


@bp.route("/pay/mock/<token>/confirm", methods=["POST"])
def mock_pay_confirm(token):
    if current_app.config["PAYMENT_PROVIDER"] != "mock":
        abort(404)
    o = _order(token)
    if sv.mark_paid(o, "mock", "MOCK-" + sv.gen_token()[:8]):
        audit("payment.paid", "order", o["id"], "mock")
        flash("تم الدفع بنجاح، يمكنك الآن إرسال الطلب", "ok")
    return redirect(url_for("public.order_status", token=token))


@bp.route("/order/<token>/send", methods=["POST"])
def order_send(token):
    o = _order(token)
    if o["status"] != "paid":
        flash("لا يمكن الإرسال قبل تأكيد الدفع", "error")
        return redirect(url_for("public.order_status", token=token))
    db.ex("UPDATE orders SET status='awaiting_doctor', sent_at=? WHERE id=? AND status='paid'", (sv.now(), o["id"]))
    sv.notify_doctors(o)
    audit("order.send", "order", o["id"])
    return redirect(url_for("public.order_sheet", token=token))


@bp.route("/order/<token>/sheet")
def order_sheet(token):
    o = _order(token)
    if o["status"] in ("pending_payment", "paid"):
        return redirect(url_for("public.order_status", token=token))
    return render_sheet(o)


def render_sheet(o):
    url = sv.public_url("public.result", token=o["token"])
    return render_template("sheet.html", o=o, h=sv.hospital_row(o["hospital_id"]), qr=sv.qr_svg(url), url=url)


# ---------------- استلام النتيجة ----------------
def _verify_page(phone, nxt, err=None):
    return render_template("verify.html", masked=sv.mask_phone(phone), nxt=nxt, err=err)


@bp.route("/results")
def results():
    phone = sv.verified_phone()
    rows = []
    if phone:
        rows = db.q("""SELECT o.*, h.id hid FROM orders o LEFT JOIN hospitals h ON h.id=o.hospital_id
                       WHERE o.phone=? AND o.status IN ('completed','delivered') AND o.published=1 ORDER BY o.completed_at DESC""", (phone,))
    return render_template("results.html", phone=phone, rows=rows)


@bp.route("/otp", methods=["POST"])
def otp():
    token = request.form.get("token")
    if token:
        phone = _order(token)["phone"]
        nxt = url_for("public.result", token=token)
    else:
        phone = sv.normalize_phone(request.form.get("phone"))
        nxt = url_for("public.results")
        if not sv.valid_phone(phone):
            flash("رقم الموبايل غير صحيح", "error")
            return redirect(url_for("public.results"))
    session["otp_phone"] = phone
    code, err = sv.issue_otp(phone)
    if err:
        flash(err, "error")
    elif current_app.config["DEV_SHOW_OTP"] and current_app.config["SMS_PROVIDER"] == "console":
        flash(f"وضع التطوير: رمز التحقق {code}", "ok")
    audit("otp.request", "phone", None, sv.mask_phone(phone))
    return _verify_page(phone, nxt)


@bp.route("/verify", methods=["POST"])
def verify():
    phone, nxt = session.get("otp_phone"), _safe_next(request.form.get("next")) or url_for("public.results")
    if not phone:
        return redirect(url_for("public.results"))
    ok, err = sv.check_otp(phone, request.form.get("code", ""))
    if not ok:
        return _verify_page(phone, nxt, err), 400
    sv.grant_verification(phone)
    session.pop("otp_phone", None)
    audit("otp.verified", "phone", None, sv.mask_phone(phone))
    return redirect(nxt)


@bp.route("/r/<token>")
def result(token):
    o = _order(token)
    if not (o["status"] in sv.DONE and o["published"]):
        return render_template("result_wait.html", o=o)
    if sv.verified_phone() == o["phone"]:
        if o["status"] != "delivered":
            db.ex("UPDATE orders SET status='delivered', delivered_at=? WHERE id=?", (sv.now(), o["id"]))
        audit("result.view", "order", o["id"])
        return render_template("report.html", public=True, versions=[], **sv.report_context(o["id"]))
    return render_template("result_gate.html", o=o, masked=sv.mask_phone(o["phone"]))
