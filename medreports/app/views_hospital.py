from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import db, services as sv
from .security import (IMAGE_EXT, attach_files, audit, bad_files, current_user, hospital_of_user, login_required,
                       save_branding)
from .views_public import render_sheet

bp = Blueprint("hospital", __name__, url_prefix="/h")


def own_order(oid):
    o = db.q1("SELECT * FROM orders WHERE id=? AND hospital_id=?", (oid, hospital_of_user()["id"]))
    if not o:
        abort(404)
    return o


@bp.route("/")
@login_required("hospital")
def dashboard():
    h = hospital_of_user()
    counts = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM orders WHERE hospital_id=? GROUP BY status", (h["id"],))}
    latest = db.q("SELECT * FROM orders WHERE hospital_id=? ORDER BY id DESC LIMIT 8", (h["id"],))
    return render_template("h_dashboard.html", h=h, counts=counts, latest=latest, order_status=sv.STATUS)


@bp.route("/bookings")
@login_required("hospital")
def bookings():
    rows = db.q("SELECT * FROM orders WHERE hospital_id=? AND status='booked' ORDER BY id DESC", (hospital_of_user()["id"],))
    return render_template("h_bookings.html", rows=rows)


@bp.route("/bookings/new", methods=["GET", "POST"])
@login_required("hospital")
def booking_new():
    if request.method == "POST":
        f = request.form
        name = " ".join(f.get("name", "").split())
        phone = sv.normalize_phone(f.get("phone"))
        age = f.get("age", type=int)
        errs = []
        if len(name.split()) < 2:
            errs.append("اسم المريض مطلوب")
        if not sv.valid_phone(phone):
            errs.append("رقم الموبايل غير صحيح (مثال 07701234567)")
        if age is not None and not 0 <= age <= 120:
            errs.append("العمر غير صحيح")
        if errs:
            for e in errs:
                flash(e, "error")
            return render_template("h_booking_form.html", f=f), 400
        oid = db.ex("""INSERT INTO orders(ref,token,source,hospital_id,patient_name,age,sex,phone,clinical_info,status,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (sv.gen_ref(), sv.gen_token(), "hospital", hospital_of_user()["id"], name, age, f.get("sex") or None,
                     phone, f.get("clinical_info", "").strip()[:2000], "booked", sv.now()))
        audit("booking.create", "order", oid)
        if f.get("action") == "continue":
            return redirect(url_for("hospital.exam", oid=oid))
        return redirect(url_for("hospital.booking_sheet", oid=oid))
    return render_template("h_booking_form.html", f={})


@bp.route("/bookings/<int:oid>/sheet")
@login_required("hospital")
def booking_sheet(oid):
    return render_sheet(own_order(oid))


@bp.route("/exams")
@login_required("hospital")
def exams():
    st = request.args.get("status", "")
    sql, args = "SELECT * FROM orders WHERE hospital_id=?", [hospital_of_user()["id"]]
    if st in sv.STATUS:
        sql += " AND status=?"
        args.append(st)
    rows = db.q(sql + " ORDER BY id DESC LIMIT 300", args)
    return render_template("h_exams.html", rows=rows, st=st, statuses=sv.STATUS)


@bp.route("/exams/<int:oid>", methods=["GET", "POST"])
@login_required("hospital")
def exam(oid):
    h = hospital_of_user()
    o = own_order(oid)
    editable = o["status"] in ("booked", "awaiting_upload")
    types = db.q("""SELECT e.*, COALESCE(hp.price, e.base_price) price FROM exam_types e
                    LEFT JOIN hospital_prices hp ON hp.exam_type_id=e.id AND hp.hospital_id=?
                    WHERE e.active=1 ORDER BY e.modality, e.region""", (h["id"],))
    files = db.q("SELECT * FROM files WHERE order_id=? ORDER BY kind, id", (oid,))
    if request.method == "POST":
        if not editable:
            abort(400, "لا يمكن تعديل الفحص بعد إرساله للطبيب")
        f = request.form
        up = [request.files.get(k) for k in ("request_sheet",)] + request.files.getlist("images") + request.files.getlist("previous")
        bad = bad_files(up)
        if bad:
            flash("نوع الملف غير مسموح: " + "، ".join(bad), "error")
            return redirect(url_for("hospital.exam", oid=oid))
        et = db.q1("SELECT * FROM exam_types WHERE id=? AND active=1", (f.get("exam_type_id", type=int),))
        db.ex("""UPDATE orders SET device=?, protocol=?, contrast=?, clinical_info=?, referring_doctor=?,
                 exam_type_id=?, exam_name=? WHERE id=?""",
              (f.get("device", "")[:100], f.get("protocol", "")[:200], f.get("contrast", "")[:100],
               f.get("clinical_info", "")[:2000], f.get("referring_doctor", "")[:100],
               et["id"] if et else None, sv.exam_name(et) if et else None, oid))
        uid = current_user()["id"]
        attach_files(oid, "request_sheet", [request.files.get("request_sheet")], uid)
        attach_files(oid, "image", request.files.getlist("images"), uid)
        attach_files(oid, "previous", request.files.getlist("previous"), uid)
        if f.get("action") == "send":
            o = db.q1("SELECT * FROM orders WHERE id=?", (oid,))
            n_img = db.q1("SELECT COUNT(*) c FROM files WHERE order_id=? AND kind='image'", (oid,))["c"]
            if not et:
                flash("اختر اسم الفحص والمنطقة قبل الإرسال", "error")
            elif n_img == 0:
                flash("ارفع صور الفحص قبل الإرسال", "error")
            else:
                db.ex("UPDATE orders SET status='awaiting_doctor', price=?, sent_at=? WHERE id=?",
                      (sv.hospital_price(h["id"], et["id"]), sv.now(), oid))
                sv.notify_doctors(db.q1("SELECT * FROM orders WHERE id=?", (oid,)))
                audit("exam.send", "order", oid)
                flash("تم إرسال الفحص للطبيب المختص", "ok")
                return redirect(url_for("hospital.exams"))
        else:
            db.ex("UPDATE orders SET status='awaiting_upload' WHERE id=? AND status='booked'", (oid,))
            audit("exam.save", "order", oid)
            flash("تم الحفظ", "ok")
        return redirect(url_for("hospital.exam", oid=oid))
    return render_template("h_exam_form.html", o=o, types=types, files=files, editable=editable)


@bp.route("/reports")
@login_required("hospital")
def reports():
    rows = db.q(f"SELECT * FROM orders WHERE hospital_id=? AND status IN {sv.DONE_SQL} ORDER BY completed_at DESC LIMIT 300",
                (hospital_of_user()["id"],))
    return render_template("h_reports.html", rows=rows)


@bp.route("/reports/<int:oid>/approve", methods=["POST"])
@login_required("hospital")
def approve(oid):
    o = own_order(oid)
    if o["status"] in sv.DONE and not o["published"]:
        db.ex("UPDATE orders SET published=1 WHERE id=?", (oid,))
        audit("report.publish", "order", oid, "hospital approval")
        flash("تم اعتماد نشر التقرير للمريض", "ok")
    return redirect(url_for("hospital.reports"))


@bp.route("/stats")
@login_required("hospital")
def stats():
    h = hospital_of_user()
    m = request.args.get("month", "")
    m = m if sv.valid_month(m) else sv.now()[:7]
    return render_template("inventory.html", title="الإحصائيات والجرد الشهري", month=m, s=sv.party_stats("hospital", h["id"], m),
                           rows=sv.by_type("hospital", h["id"], m), endpoint="hospital.stats", kind="مستشفى")


@bp.route("/settings", methods=["GET", "POST"])
@login_required("hospital")
def settings():
    h = hospital_of_user()
    if request.method == "POST":
        f = request.form
        try:
            logo = save_branding(request.files.get("logo")) or h["logo"]
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("hospital.settings"))
        db.ex("UPDATE users SET phone=? WHERE id=?", (f.get("phone", "")[:30], h["user_id"]), commit=False)
        db.ex("UPDATE hospitals SET address=?, receive_notes=?, logo=? WHERE id=?",
              (f.get("address", "")[:300], f.get("receive_notes", "")[:600], logo, h["id"]))
        audit("hospital.settings", "hospital", h["id"])
        flash("تم حفظ الإعدادات", "ok")
        return redirect(url_for("hospital.settings"))
    return render_template("h_settings.html", h=h)
