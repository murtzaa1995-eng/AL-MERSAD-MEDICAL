import os
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, services as sv
from .security import audit, current_user, login_required

bp = Blueprint("common", __name__)


def can_view_order(u, o):
    if u["role"] == "admin":
        return True
    if u["role"] == "hospital":
        h = db.q1("SELECT id FROM hospitals WHERE user_id=?", (u["id"],))
        return bool(h and o["hospital_id"] == h["id"])
    d = db.q1("SELECT id FROM doctors WHERE user_id=?", (u["id"],))
    return bool(d and sv.doctor_can_see(d["id"], o))


@bp.route("/files/<int:fid>")
@login_required()
def file(fid):
    f = db.q1("SELECT * FROM files WHERE id=?", (fid,))
    o = db.q1("SELECT * FROM orders WHERE id=?", (f["order_id"],)) if f else None
    if not f or not can_view_order(current_user(), o):
        abort(404)
    audit("file.open", "order", o["id"], f["original_name"])
    inline = f["stored_name"].rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "gif", "webp", "pdf")
    return send_from_directory(os.path.join(current_app.config["UPLOAD_DIR"], "private"), f["stored_name"],
                               as_attachment=not inline, download_name=f["original_name"])


@bp.route("/media/<name>")
def media(name):
    return send_from_directory(os.path.join(current_app.config["UPLOAD_DIR"], "branding"), name)


@bp.route("/report/<int:oid>")
@login_required()
def report(oid):
    o = db.q1("SELECT * FROM orders WHERE id=?", (oid,))
    if not o or o["status"] not in sv.DONE or not can_view_order(current_user(), o):
        abort(404)
    ctx = sv.report_context(oid)
    audit("report.view", "order", oid)
    versions = db.q("SELECT version, created_at, reason FROM reports WHERE order_id=? ORDER BY version DESC", (oid,))
    ver = request.args.get("v", type=int)
    if ver and current_user()["role"] != "hospital":
        r = db.q1("SELECT * FROM reports WHERE order_id=? AND version=?", (oid, ver))
        if r:
            ctx["r"] = r
    return render_template("report.html", public=False, versions=versions, **ctx)


@bp.route("/notifications")
@login_required()
def notifications():
    u = current_user()
    rows = db.q("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100", (u["id"],))
    db.ex("UPDATE notifications SET is_read=1 WHERE user_id=?", (u["id"],))
    return render_template("notifications.html", rows=rows)


@bp.route("/account", methods=["GET", "POST"])
@login_required()
def account():
    u = current_user()
    if request.method == "POST":
        new = request.form.get("new", "")
        if not check_password_hash(u["password_hash"], request.form.get("old", "")):
            flash("كلمة السر الحالية غير صحيحة", "error")
        elif len(new) < 8:
            flash("كلمة السر الجديدة 8 أحرف على الأقل", "error")
        else:
            db.ex("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), u["id"]))
            audit("account.password", "user", u["id"])
            flash("تم تغيير كلمة السر", "ok")
            return redirect(url_for("common.account"))
    return render_template("account.html")
