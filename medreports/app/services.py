"""Business logic: time, phones, OTP, SMS, QR, payments, pricing, finance."""
import hashlib
import hmac
import logging
import re
import secrets
import time
import zipfile
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from flask import current_app, session, url_for

from . import db

log = logging.getLogger("medreports")

MODALITIES = {
    "xray": "أشعة سينية", "ct": "مفراس (CT)", "mri": "رنين (MRI)",
    "us": "سونار", "mammo": "ماموغرام", "other": "أخرى",
}
STATUS = {
    "pending_payment": "بانتظار الدفع", "paid": "مدفوع - بانتظار الإرسال",
    "booked": "محجوز", "awaiting_upload": "بانتظار الرفع",
    "awaiting_doctor": "بانتظار قراءة الطبيب", "reading": "قيد القراءة",
    "completed": "مكتمل", "delivered": "تم التسليم",
}
DONE = ("completed", "delivered")
DONE_SQL = "('completed','delivered')"


# ---------- time ----------
def now():
    off = int(current_app.config.get("TZ_OFFSET_HOURS", 3))  # بغداد UTC+3
    return (datetime.now(timezone.utc) + timedelta(hours=off)).strftime("%Y-%m-%d %H:%M:%S")


def valid_month(m):
    return bool(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m or ""))


def month_bounds(m):
    y, mo = int(m[:4]), int(m[5:7])
    ny, nm = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return f"{y:04d}-{mo:02d}-01", f"{ny:04d}-{nm:02d}-01"


# ---------- identifiers / phones ----------
def normalize_phone(p):
    d = re.sub(r"\D", "", p or "")
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("964"):
        d = "0" + d[3:]
    elif len(d) == 10 and d.startswith("7"):
        d = "0" + d
    return d


def valid_phone(d):
    return bool(re.fullmatch(r"07\d{9}", d or ""))


def mask_phone(d):
    return "*******" + d[-4:] if d else ""


_ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gen_ref():
    while True:
        n = now()
        r = f"MR-{n[2:4]}{n[5:7]}-" + "".join(secrets.choice(_ALPHA) for _ in range(5))
        if not db.q1("SELECT 1 FROM orders WHERE ref=?", (r,)):
            return r


def gen_token():
    return secrets.token_urlsafe(18)


def public_url(endpoint, **kw):
    base = current_app.config.get("PUBLIC_BASE_URL")
    if base:
        return base.rstrip("/") + url_for(endpoint, **kw)
    return url_for(endpoint, _external=True, **kw)


def qr_svg(data):
    """يتطلب مكتبة segno. إن لم تكن مثبتة يرجع None وتعرض الصفحة الرابط نصاً."""
    try:
        import segno
        return segno.make(data, error="m").svg_inline(scale=4, border=1)
    except Exception:  # pragma: no cover
        log.warning("segno غير متوفرة: pip install segno")
        return None


# ---------- notifications ----------
def notify(user_id, text, link=None):
    db.ex("INSERT INTO notifications(user_id,text,link,created_at) VALUES (?,?,?,?)",
          (user_id, text, link, now()))


def eligible_doctors(hospital_id, exam_type_id):
    return db.q(
        """SELECT d.id, d.user_id FROM doctors d JOIN users u ON u.id=d.user_id AND u.active=1
           WHERE EXISTS(SELECT 1 FROM doctor_prices dp WHERE dp.doctor_id=d.id AND dp.exam_type_id=:e)
           AND ((:h IS NOT NULL AND EXISTS(SELECT 1 FROM doctor_hospitals dh WHERE dh.doctor_id=d.id AND dh.hospital_id=:h))
             OR (:h IS NULL AND d.accept_direct=1))""",
        {"e": exam_type_id, "h": hospital_id})


def doctor_can_see(doctor_id, order):
    if order["doctor_id"] == doctor_id:
        return True
    return any(d["id"] == doctor_id for d in eligible_doctors(order["hospital_id"], order["exam_type_id"]))


def notify_doctors(order):
    for d in eligible_doctors(order["hospital_id"], order["exam_type_id"]):
        notify(d["user_id"], f"فحص جديد بانتظار القراءة: {order['exam_name']}", f"/d/orders/{order['id']}")


# ---------- pricing ----------
def hospital_price(hospital_id, exam_type_id):
    r = db.q1("SELECT price FROM hospital_prices WHERE hospital_id=? AND exam_type_id=?", (hospital_id, exam_type_id))
    if r:
        return r["price"]
    e = db.q1("SELECT base_price FROM exam_types WHERE id=?", (exam_type_id,))
    return e["base_price"] if e else 0


def exam_name(e):
    return f"{MODALITIES.get(e['modality'], e['modality'])} - {e['region']}"


# ---------- SMS / OTP ----------
def send_sms(phone, text):
    if current_app.config["SMS_PROVIDER"] == "console":
        log.warning("SMS -> %s : %s", phone, text)
        return True
    raise NotImplementedError("اربط مزود الرسائل هنا (SMS_PROVIDER).")


def _otp_hash(phone, code):
    return hmac.new(current_app.config["SECRET_KEY"].encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def issue_otp(phone):
    t = int(time.time())
    row = db.q1("SELECT created FROM otps WHERE phone=?", (phone,))
    if row and t - row["created"] < 60:
        return None, "انتظر دقيقة قبل طلب رمز جديد."
    code = f"{secrets.randbelow(10**6):06d}"
    db.ex("INSERT OR REPLACE INTO otps(phone,code_hash,expires_at,attempts,created) VALUES (?,?,?,?,?)",
          (phone, _otp_hash(phone, code), t + 300, 0, t))
    send_sms(phone, f"رمز التحقق لاستلام نتيجتك: {code}")
    return code, None


def check_otp(phone, code):
    t = int(time.time())
    row = db.q1("SELECT * FROM otps WHERE phone=?", (phone,))
    if not row or row["expires_at"] < t:
        return False, "انتهت صلاحية الرمز، اطلب رمزاً جديداً."
    if row["attempts"] >= 5:
        return False, "تجاوزت عدد المحاولات، اطلب رمزاً جديداً."
    db.ex("UPDATE otps SET attempts=attempts+1 WHERE phone=?", (phone,))
    if hmac.compare_digest(row["code_hash"], _otp_hash(phone, (code or "").strip())):
        db.ex("DELETE FROM otps WHERE phone=?", (phone,))
        return True, None
    return False, "الرمز غير صحيح."


def verified_phone():
    rv = session.get("rv")
    if rv and rv.get("until", 0) > time.time():
        return rv["phone"]
    session.pop("rv", None)
    return None


def grant_verification(phone):
    session["rv"] = {"phone": phone, "until": time.time() + 60 * int(current_app.config["RESULT_SESSION_MINUTES"])}


# ---------- payments (قابل للاستبدال ببوابة حقيقية) ----------
def start_checkout(order):
    """يرجع رابط صفحة الدفع. للربط مع بوابة حقيقية (ماستر كارد/زين كاش/كي كارد...) عدّل هنا
    وأنشئ webhook يستدعي mark_paid بعد تأكيد البوابة. لا تُخزَّن بيانات البطاقة في هذا النظام أبداً."""
    if current_app.config["PAYMENT_PROVIDER"] == "mock":
        return url_for("public.mock_pay", token=order["token"])
    raise NotImplementedError("PAYMENT_PROVIDER غير مُهيأ.")


def mark_paid(order, provider, ref):
    if order["status"] != "pending_payment":
        return False
    db.ex("INSERT INTO payments(order_id,amount,provider,provider_ref,status,created_at) VALUES (?,?,?,?,?,?)",
          (order["id"], order["price"], provider, ref, "paid", now()), commit=False)
    db.ex("UPDATE orders SET status='paid' WHERE id=?", (order["id"],), commit=False)
    db.commit()
    return True


# ---------- docx ----------
def docx_text(fileobj):
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(fileobj) as z:
        info = z.getinfo("word/document.xml")
        if info.file_size > 5_000_000:
            raise ValueError("ملف Word كبير جداً")
        root = ET.fromstring(z.read("word/document.xml"))
    out = []
    for p in root.iter(ns + "p"):
        s = ""
        for t in p.iter():
            if t.tag == ns + "t":
                s += t.text or ""
            elif t.tag == ns + "tab":
                s += "\t"
            elif t.tag == ns + "br":
                s += "\n"
        out.append(s)
    return "\n".join(out).strip()


# ---------- report / finance ----------
def hospital_row(hid):
    if not hid:
        return None
    return db.q1("SELECT h.*, u.name, u.phone, u.email FROM hospitals h JOIN users u ON u.id=h.user_id WHERE h.id=?", (hid,))


def report_context(order_id):
    o = db.q1("SELECT o.*, e.modality, e.region FROM orders o LEFT JOIN exam_types e ON e.id=o.exam_type_id WHERE o.id=?", (order_id,))
    d = db.q1("SELECT d.*, u.name FROM doctors d JOIN users u ON u.id=d.user_id WHERE d.id=?", (o["doctor_id"],)) if o["doctor_id"] else None
    r = db.q1("SELECT * FROM reports WHERE order_id=? ORDER BY version DESC LIMIT 1", (order_id,))
    return {"o": o, "h": hospital_row(o["hospital_id"]), "d": d, "r": r}


_COL = {"hospital": ("hospital_id", "price"), "doctor": ("doctor_id", "doctor_fee")}


def party_stats(kind, pid, month):
    col, amt = _COL[kind]
    s, e = month_bounds(month)
    m = db.q1(f"SELECT COUNT(*) n, COALESCE(SUM({amt}),0) t FROM orders WHERE {col}=? AND status IN {DONE_SQL} AND completed_at>=? AND completed_at<?", (pid, s, e))
    accrued = db.q1(f"SELECT COALESCE(SUM({amt}),0) t FROM orders WHERE {col}=? AND status IN {DONE_SQL}", (pid,))["t"]
    paid = db.q1("SELECT COALESCE(SUM(amount),0) p FROM settlements WHERE party_type=? AND party_id=?", (kind, pid))["p"]
    return {"count": m["n"], "month_total": m["t"], "accrued": accrued, "paid": paid, "balance": accrued - paid}


def by_type(kind, pid, month):
    col, amt = _COL[kind]
    s, e = month_bounds(month)
    return db.q(f"""SELECT o.exam_name name, COUNT(*) n, COALESCE(SUM(o.{amt}),0) total FROM orders o
                    WHERE o.{col}=? AND o.status IN {DONE_SQL} AND o.completed_at>=? AND o.completed_at<?
                    GROUP BY o.exam_name ORDER BY n DESC""", (pid, s, e))


def balance(kind, pid):
    return party_stats(kind, pid, "2000-01")["balance"]
