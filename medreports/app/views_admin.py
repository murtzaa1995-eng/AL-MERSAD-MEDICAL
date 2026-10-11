from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import db, services as sv
from .security import audit, create_user, current_user, login_required, save_branding

bp = Blueprint("admin", __name__, url_prefix="/admin")
ADMIN = login_required("admin")


def _month():
    m = request.args.get("month", "")
    return m if sv.valid_month(m) else sv.now()[:7]


@bp.route("/")
@ADMIN
def dashboard():
    m = sv.now()[:7]
    s, e = sv.month_bounds(m)
    tot = db.q1(f"SELECT COUNT(*) n, COALESCE(SUM(price),0) rev, COALESCE(SUM(doctor_fee),0) dues FROM orders WHERE status IN {sv.DONE_SQL} AND completed_at>=? AND completed_at<?", (s, e))
    counts = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM orders GROUP BY status")}
    return render_template("a_dashboard.html", month=m, tot=tot, counts=counts, statuses=sv.STATUS,
                           hospitals=db.q1("SELECT COUNT(*) c FROM hospitals")["c"], doctors=db.q1("SELECT COUNT(*) c FROM doctors")["c"])


# ---------- helpers ----------
def _email_taken(email, uid=None):
    r = db.q1("SELECT id FROM users WHERE email=?", (email.strip().lower(),))
    return bool(r and r["id"] != uid)


def _prices(table, owner_col, owner_id, f, allowed_only=False):
    db.ex(f"DELETE FROM {table} WHERE {owner_col}=?", (owner_id,), commit=False)
    for e in db.q("SELECT id FROM exam_types"):
        v = f.get(f"price_{e['id']}", "").strip()
        if allowed_only and not f.get(f"allow_{e['id']}"):
            continue
        if v.isdigit():
            db.ex(f"INSERT INTO {table}({owner_col},exam_type_id,price) VALUES (?,?,?)", (owner_id, e["id"], int(v)), commit=False)


# ---------- hospitals ----------
@bp.route("/hospitals")
@ADMIN
def hospitals():
    rows = db.q("SELECT h.id, u.name, u.email, u.phone, u.active, h.publish_mode FROM hospitals h JOIN users u ON u.id=h.user_id ORDER BY u.name")
    return render_template("a_hospitals.html", rows=rows)


@bp.route("/hospitals/new", methods=["GET", "POST"])
@bp.route("/hospitals/<int:hid>", methods=["GET", "POST"])
@ADMIN
def hospital_form(hid=None):
    h = sv.hospital_row(hid) if hid else None
    if hid and not h:
        abort(404)
    types = db.q("SELECT * FROM exam_types ORDER BY modality, region")
    prices = {r["exam_type_id"]: r["price"] for r in db.q("SELECT * FROM hospital_prices WHERE hospital_id=?", (hid,))} if hid else {}
    if request.method == "POST":
        f = request.form
        name, email, pw = f.get("name", "").strip(), f.get("email", "").strip().lower(), f.get("password", "")
        errs = []
        if not name or not email:
            errs.append("الاسم والبريد مطلوبان")
        if _email_taken(email, h["user_id"] if h else None):
            errs.append("البريد مستخدم مسبقاً")
        if (not h and len(pw) < 8) or (pw and len(pw) < 8):
            errs.append("كلمة السر 8 أحرف على الأقل")
        try:
            logo = save_branding(request.files.get("logo")) or (h["logo"] if h else None)
        except ValueError as e:
            errs.append(str(e))
        if errs:
            for e in errs:
                flash(e, "error")
            return render_template("a_hospital_form.html", h=h, v=f, types=types, prices=prices), 400
        active = 1 if f.get("active") else 0
        mode = f.get("publish_mode") if f.get("publish_mode") in ("auto", "hospital_approval") else "auto"
        cols = (f.get("address", ""), f.get("receive_notes", ""), logo, f.get("tpl_header", ""), f.get("tpl_body", ""), f.get("tpl_footer", ""), mode)
        if h:
            db.ex("UPDATE users SET name=?, email=?, phone=?, active=? WHERE id=?", (name, email, f.get("phone", ""), active, h["user_id"]), commit=False)
            if pw:
                db.ex("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), h["user_id"]), commit=False)
            db.ex("UPDATE hospitals SET address=?, receive_notes=?, logo=?, tpl_header=?, tpl_body=?, tpl_footer=?, publish_mode=? WHERE id=?", cols + (h["id"],), commit=False)
            hid = h["id"]
        else:
            uid = create_user("hospital", name, email, f.get("phone", ""), pw, active, commit=False)
            hid = db.ex("INSERT INTO hospitals(user_id,address,receive_notes,logo,tpl_header,tpl_body,tpl_footer,publish_mode) VALUES (?,?,?,?,?,?,?,?)", (uid,) + cols, commit=False)
        _prices("hospital_prices", "hospital_id", hid, f)
        db.commit()
        audit("hospital.save", "hospital", hid, name)
        flash("تم الحفظ", "ok")
        return redirect(url_for("admin.hospitals"))
    return render_template("a_hospital_form.html", h=h, v=(dict(h) if h else {}), types=types, prices=prices)


# ---------- doctors ----------
@bp.route("/doctors")
@ADMIN
def doctors():
    rows = db.q("SELECT d.id, u.name, u.email, u.phone, u.active, d.specialty FROM doctors d JOIN users u ON u.id=d.user_id ORDER BY u.name")
    return render_template("a_doctors.html", rows=rows)


@bp.route("/doctors/new", methods=["GET", "POST"])
@bp.route("/doctors/<int:did>", methods=["GET", "POST"])
@ADMIN
def doctor_form(did=None):
    d = db.q1("SELECT d.*, u.name, u.email, u.phone, u.active FROM doctors d JOIN users u ON u.id=d.user_id WHERE d.id=?", (did,)) if did else None
    if did and not d:
        abort(404)
    types = db.q("SELECT * FROM exam_types ORDER BY modality, region")
    hospitals = db.q("SELECT h.id, u.name FROM hospitals h JOIN users u ON u.id=h.user_id ORDER BY u.name")
    prices = {r["exam_type_id"]: r["price"] for r in db.q("SELECT * FROM doctor_prices WHERE doctor_id=?", (did,))} if did else {}
    my_h = {r["hospital_id"] for r in db.q("SELECT hospital_id FROM doctor_hospitals WHERE doctor_id=?", (did,))} if did else set()
    ctx = dict(d=d, types=types, hospitals=hospitals, prices=prices, my_h=my_h)
    if request.method == "POST":
        f = request.form
        name, email, pw = f.get("name", "").strip(), f.get("email", "").strip().lower(), f.get("password", "")
        errs = []
        if not name or not email:
            errs.append("الاسم والبريد مطلوبان")
        if _email_taken(email, d["user_id"] if d else None):
            errs.append("البريد مستخدم مسبقاً")
        if (not d and len(pw) < 8) or (pw and len(pw) < 8):
            errs.append("كلمة السر 8 أحرف على الأقل")
        try:
            stamp = save_branding(request.files.get("stamp")) or (d["stamp"] if d else None)
            sig = save_branding(request.files.get("signature")) or (d["signature"] if d else None)
        except ValueError as e:
            errs.append(str(e))
        if errs:
            for e in errs:
                flash(e, "error")
            return render_template("a_doctor_form.html", v=f, **ctx), 400
        active, direct = (1 if f.get("active") else 0), (1 if f.get("accept_direct") else 0)
        if d:
            db.ex("UPDATE users SET name=?, email=?, phone=?, active=? WHERE id=?", (name, email, f.get("phone", ""), active, d["user_id"]), commit=False)
            if pw:
                db.ex("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), d["user_id"]), commit=False)
            db.ex("UPDATE doctors SET specialty=?, stamp=?, signature=?, accept_direct=? WHERE id=?", (f.get("specialty", ""), stamp, sig, direct, d["id"]), commit=False)
            did = d["id"]
        else:
            uid = create_user("doctor", name, email, f.get("phone", ""), pw, active, commit=False)
            did = db.ex("INSERT INTO doctors(user_id,specialty,stamp,signature,accept_direct) VALUES (?,?,?,?,?)", (uid, f.get("specialty", ""), stamp, sig, direct), commit=False)
        db.ex("DELETE FROM doctor_hospitals WHERE doctor_id=?", (did,), commit=False)
        for hid in f.getlist("hospitals"):
            if hid.isdigit():
                db.ex("INSERT INTO doctor_hospitals(doctor_id,hospital_id) VALUES (?,?)", (did, int(hid)), commit=False)
        _prices("doctor_prices", "doctor_id", did, f, allowed_only=True)
        db.commit()
        audit("doctor.save", "doctor", did, name)
        flash("تم الحفظ", "ok")
        return redirect(url_for("admin.doctors"))
    return render_template("a_doctor_form.html", v=(dict(d) if d else {}), **ctx)


# ---------- exam types ----------
@bp.route("/exam-types", methods=["GET", "POST"])
@ADMIN
def exam_types():
    if request.method == "POST":
        f = request.form
        region, price = f.get("region", "").strip(), f.get("base_price", "")
        if f.get("modality") not in sv.MODALITIES or not region or not price.isdigit():
            flash("بيانات غير صحيحة", "error")
        elif f.get("id"):
            db.ex("UPDATE exam_types SET modality=?, region=?, base_price=?, active=? WHERE id=?",
                  (f["modality"], region, int(price), 1 if f.get("active") else 0, f.get("id", type=int)))
            audit("examtype.update", "exam_type", f.get("id", type=int))
        else:
            i = db.ex("INSERT INTO exam_types(modality,region,base_price) VALUES (?,?,?)", (f["modality"], region, int(price)))
            audit("examtype.create", "exam_type", i)
        return redirect(url_for("admin.exam_types"))
    return render_template("a_exams.html", rows=db.q("SELECT * FROM exam_types ORDER BY modality, region"))


# ---------- orders ----------
@bp.route("/orders")
@ADMIN
def orders():
    st, qtxt = request.args.get("status", ""), request.args.get("q", "").strip()
    sql, args = "SELECT o.*, hu.name hospital_name FROM orders o LEFT JOIN hospitals h ON h.id=o.hospital_id LEFT JOIN users hu ON hu.id=h.user_id WHERE 1=1", []
    if st in sv.STATUS:
        sql += " AND o.status=?"
        args.append(st)
    if qtxt:
        sql += " AND (o.ref LIKE ? OR o.patient_name LIKE ? OR o.phone LIKE ?)"
        args += [f"%{qtxt}%"] * 3
    return render_template("a_orders.html", rows=db.q(sql + " ORDER BY o.id DESC LIMIT 300", args), st=st, q=qtxt, statuses=sv.STATUS)


@bp.route("/orders/<int:oid>")
@ADMIN
def order(oid):
    o = db.q1("SELECT o.*, hu.name hospital_name, du.name doctor_name FROM orders o LEFT JOIN hospitals h ON h.id=o.hospital_id LEFT JOIN users hu ON hu.id=h.user_id LEFT JOIN doctors d ON d.id=o.doctor_id LEFT JOIN users du ON du.id=d.user_id WHERE o.id=?", (oid,))
    if not o:
        abort(404)
    return render_template("a_order.html", o=o, files=db.q("SELECT * FROM files WHERE order_id=?", (oid,)),
                           log=db.q("SELECT * FROM audit_log WHERE entity='order' AND entity_id=? ORDER BY id", (oid,)),
                           pays=db.q("SELECT * FROM payments WHERE order_id=?", (oid,)))


# ---------- finance ----------
@bp.route("/finance")
@ADMIN
def finance():
    m = _month()
    s, e = sv.month_bounds(m)
    hf, df = request.args.get("hospital", type=int), request.args.get("doctor", type=int)
    hs = [dict(r) for r in db.q("SELECT h.id, u.name FROM hospitals h JOIN users u ON u.id=h.user_id ORDER BY u.name")]
    ds = [dict(r) for r in db.q("SELECT d.id, u.name FROM doctors d JOIN users u ON u.id=d.user_id ORDER BY u.name")]
    hrows = [dict(x, **sv.party_stats("hospital", x["id"], m)) for x in hs if not hf or x["id"] == hf]
    drows = [dict(x, **sv.party_stats("doctor", x["id"], m)) for x in ds if not df or x["id"] == df]
    direct = db.q1(f"SELECT COUNT(*) n, COALESCE(SUM(price),0) t FROM orders WHERE source='patient' AND status IN {sv.DONE_SQL} AND completed_at>=? AND completed_at<?", (s, e))
    paid_online = db.q1("SELECT COUNT(*) n, COALESCE(SUM(amount),0) t FROM payments WHERE status='paid' AND created_at>=? AND created_at<?", (s, e))
    tot = db.q1(f"SELECT COALESCE(SUM(price),0) rev, COALESCE(SUM(doctor_fee),0) dues FROM orders WHERE status IN {sv.DONE_SQL} AND completed_at>=? AND completed_at<?", (s, e))
    recent = db.q("SELECT s.*, CASE s.party_type WHEN 'hospital' THEN (SELECT u.name FROM hospitals h JOIN users u ON u.id=h.user_id WHERE h.id=s.party_id) ELSE (SELECT u.name FROM doctors d JOIN users u ON u.id=d.user_id WHERE d.id=s.party_id) END pname FROM settlements s ORDER BY s.id DESC LIMIT 15")
    return render_template("a_finance.html", month=m, hs=hs, ds=ds, hf=hf, df=df, hrows=hrows, drows=drows,
                           direct=direct, paid_online=paid_online, tot=tot, recent=recent)


@bp.route("/finance/settle", methods=["POST"])
@ADMIN
def settle():
    f = request.form
    kind, pid, amt = f.get("party_type"), f.get("party_id", type=int), f.get("amount", "").strip()
    if kind not in ("hospital", "doctor") or not pid or not amt.isdigit() or int(amt) <= 0:
        flash("المبلغ غير صحيح", "error")
    else:
        bal = sv.balance(kind, pid)
        if int(amt) > bal:
            flash(f"المبلغ أكبر من الرصيد المستحق ({bal:,})", "error")
        else:
            db.ex("INSERT INTO settlements(party_type,party_id,amount,note,created_by,created_at) VALUES (?,?,?,?,?,?)",
                  (kind, pid, int(amt), f.get("note", "")[:200], current_user()["id"], sv.now()))
            audit("finance.settle", kind, pid, f"{amt} (المتبقي {bal - int(amt)})")
            flash(f"تم تسجيل الدفع، المتبقي في الحساب: {bal - int(amt):,}", "ok")
    return redirect(request.referrer or url_for("admin.finance"))


@bp.route("/audit")
@ADMIN
def audit_log():
    rows = db.q("SELECT a.*, u.name uname FROM audit_log a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 300")
    return render_template("a_audit.html", rows=rows)
