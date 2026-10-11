import io
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import db, services as sv
from .security import audit, doctor_of_user, login_required

bp = Blueprint("doctor", __name__, url_prefix="/d")

BASE = """SELECT o.*, e.modality, e.region, hu.name hospital_name FROM orders o
          LEFT JOIN exam_types e ON e.id=o.exam_type_id
          LEFT JOIN hospitals hp ON hp.id=o.hospital_id LEFT JOIN users hu ON hu.id=hp.user_id"""

ELIGIBLE = """o.status='awaiting_doctor' AND o.exam_type_id IN (SELECT exam_type_id FROM doctor_prices WHERE doctor_id=:d)
  AND ((o.hospital_id IS NOT NULL AND o.hospital_id IN (SELECT hospital_id FROM doctor_hospitals WHERE doctor_id=:d))
    OR (o.hospital_id IS NULL AND (SELECT accept_direct FROM doctors WHERE id=:d)=1))"""


def get_order(oid):
    d = doctor_of_user()
    o = db.q1(BASE + " WHERE o.id=?", (oid,))
    if not o or not sv.doctor_can_see(d["id"], o):
        abort(404)
    return d, o


@bp.route("/")
@login_required("doctor")
def dashboard():
    d = doctor_of_user()
    waiting = db.q1(f"SELECT COUNT(*) c FROM orders o WHERE {ELIGIBLE}", {"d": d["id"]})["c"]
    reading = db.q1("SELECT COUNT(*) c FROM orders WHERE doctor_id=? AND status='reading'", (d["id"],))["c"]
    s = sv.party_stats("doctor", d["id"], sv.now()[:7])
    return render_template("d_dashboard.html", d=d, waiting=waiting, reading=reading, s=s)


@bp.route("/worklist")
@login_required("doctor")
def worklist():
    d = doctor_of_user()
    rows = db.q(f"{BASE} WHERE {ELIGIBLE} ORDER BY o.sent_at", {"d": d["id"]})
    return render_template("d_worklist.html", rows=rows)


@bp.route("/mine")
@login_required("doctor")
def mine():
    d = doctor_of_user()
    rows = db.q(f"{BASE} WHERE o.doctor_id=? ORDER BY o.id DESC LIMIT 300", (d["id"],))
    return render_template("d_mine.html", rows=rows)


@bp.route("/orders/<int:oid>")
@login_required("doctor")
def order(oid):
    d, o = get_order(oid)
    files = db.q("SELECT * FROM files WHERE order_id=? ORDER BY kind, id", (oid,))
    audit("order.open", "order", oid)
    return render_template("d_order.html", o=o, d=d, files=files)


@bp.route("/orders/<int:oid>/start", methods=["POST"])
@login_required("doctor")
def start(oid):
    d, o = get_order(oid)
    fee = db.q1("SELECT price FROM doctor_prices WHERE doctor_id=? AND exam_type_id=?", (d["id"], o["exam_type_id"]))
    if not fee:
        abort(403)
    cur = db.get_db().execute("UPDATE orders SET status='reading', doctor_id=?, doctor_fee=? WHERE id=? AND status='awaiting_doctor'",
                              (d["id"], fee["price"], oid))
    db.commit()
    if cur.rowcount:
        audit("order.start_reading", "order", oid)
        return redirect(url_for("doctor.editor", oid=oid))
    flash("سبقك طبيب آخر في فتح هذا الفحص", "error")
    return redirect(url_for("doctor.worklist"))


@bp.route("/orders/<int:oid>/release", methods=["POST"])
@login_required("doctor")
def release(oid):
    d, o = get_order(oid)
    if o["doctor_id"] == d["id"] and o["status"] == "reading":
        db.ex("UPDATE orders SET status='awaiting_doctor', doctor_id=NULL, doctor_fee=0 WHERE id=?", (oid,))
        audit("order.release", "order", oid)
        flash("أُعيد الفحص إلى قائمة الانتظار", "ok")
    return redirect(url_for("doctor.worklist"))


@bp.route("/orders/<int:oid>/report", methods=["GET", "POST"])
@login_required("doctor")
def editor(oid):
    d, o = get_order(oid)
    if o["doctor_id"] != d["id"] or o["status"] not in ("reading", "completed", "delivered"):
        abort(403)
    correcting = o["status"] in sv.DONE
    last = db.q1("SELECT * FROM reports WHERE order_id=? ORDER BY version DESC LIMIT 1", (oid,))
    hosp = sv.hospital_row(o["hospital_id"])
    body = (o["draft"] if o["draft"] is not None else None)
    if body is None:
        body = last["body"] if correcting else ((hosp["tpl_body"] if hosp else "") or "")
    if request.method == "POST":
        act, body = request.form.get("action"), request.form.get("body", "")
        if act == "import":
            f = request.files.get("docx")
            try:
                body = sv.docx_text(io.BytesIO(f.read())) if f and f.filename.lower().endswith(".docx") else body
                flash("تم استيراد نص الملف إلى المحرر، راجعه قبل الاعتماد", "ok")
            except Exception:
                flash("تعذر قراءة ملف Word (يجب أن يكون .docx)", "error")
            db.ex("UPDATE orders SET draft=? WHERE id=?", (body, oid))
        elif act == "save":
            db.ex("UPDATE orders SET draft=? WHERE id=?", (body, oid))
            flash("تم حفظ المسودة", "ok")
        elif act == "sign":
            reason = request.form.get("reason", "").strip()
            if not body.strip():
                flash("التقرير فارغ", "error")
            elif correcting and not reason:
                flash("اكتب سبب التصحيح", "error")
            else:
                ver = (last["version"] + 1) if last else 1
                db.ex("INSERT INTO reports(order_id,version,body,doctor_id,reason,created_at) VALUES (?,?,?,?,?,?)",
                      (oid, ver, body.strip(), d["id"], reason or None, sv.now()), commit=False)
                if not correcting:
                    pub = 1 if (not hosp or hosp["publish_mode"] == "auto") else 0
                    db.ex("UPDATE orders SET status='completed', completed_at=?, published=?, draft=NULL WHERE id=?",
                          (sv.now(), pub, oid), commit=False)
                else:
                    db.ex("UPDATE orders SET draft=NULL WHERE id=?", (oid,), commit=False)
                db.commit()
                audit("report.correct" if correcting else "report.sign", "order", oid, f"v{ver}" + (f": {reason}" if reason else ""))
                if hosp:
                    msg = (f"تم تصحيح تقرير {o['patient_name']} (نسخة {ver})" if correcting else
                           f"اكتمل تقرير {o['patient_name']}" + ("" if hosp["publish_mode"] == "auto" else " - بانتظار موافقتكم على النشر"))
                    sv.notify(hosp["user_id"], msg, "/h/reports")
                flash("تم اعتماد التقرير وإرساله", "ok")
                return redirect(url_for("doctor.mine"))
        return render_template("d_editor.html", o=o, d=d, body=body, correcting=correcting, hosp=hosp, last=last)
    return render_template("d_editor.html", o=o, d=d, body=body, correcting=correcting, hosp=hosp, last=last)


@bp.route("/stats")
@login_required("doctor")
def stats():
    d = doctor_of_user()
    m = request.args.get("month", "")
    m = m if sv.valid_month(m) else sv.now()[:7]
    return render_template("inventory.html", title="الجرد الشهري", month=m, s=sv.party_stats("doctor", d["id"], m),
                           rows=sv.by_type("doctor", d["id"], m), endpoint="doctor.stats", kind="طبيب")
