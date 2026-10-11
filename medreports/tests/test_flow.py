"""اختبار شامل للمسار: إدارة -> مستشفى -> طبيب -> مريض -> جرد مالي."""
import io
import re
import tempfile
import unittest
import zipfile

from app import create_app, db

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50


def make_docx(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return b.getvalue()


class Flow(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.app = create_app({"TESTING": True, "DATABASE": d + "/t.db", "UPLOAD_DIR": d + "/u",
                               "BOOTSTRAP_ADMIN_EMAIL": "admin@t.test", "BOOTSTRAP_ADMIN_PASSWORD": "adminpass1",
                               "SECRET_KEY": "test-secret"})

    def client(self):
        return self.app.test_client()

    def post(self, c, url, data=None, **kw):
        with c.session_transaction() as s:
            s.setdefault("csrf", "tok")
            tok = s["csrf"]
        d = dict(data or {})
        d["csrf_token"] = tok
        return c.post(url, data=d, content_type="multipart/form-data", **kw)

    def login(self, email, pw):
        c = self.client()
        r = self.post(c, "/login", {"email": email, "password": pw})
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        return c

    def test_full_flow(self):
        with self.app.app_context():
            ct_brain = db.q1("SELECT id FROM exam_types WHERE modality='ct' AND region='الدماغ'")["id"]
        # الإدارة: مستشفى (مع موافقة نشر) + طبيب
        adm = self.login("admin@t.test", "adminpass1")
        r = self.post(adm, "/admin/hospitals/new", {"name": "مستشفى الأمل", "email": "h@t.test", "password": "hospass123",
                      "phone": "07701112233", "address": "بغداد", "tpl_body": "الموجودات:", "publish_mode": "hospital_approval",
                      "active": "on", f"price_{ct_brain}": "18000", "logo": (io.BytesIO(PNG), "logo.png")})
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:400])
        with self.app.app_context():
            hid = db.q1("SELECT id FROM hospitals")["id"]
        r = self.post(adm, "/admin/doctors/new", {"name": "د. علي", "email": "d@t.test", "password": "docpass123", "specialty": "أشعة",
                      "active": "on", "hospitals": str(hid), "accept_direct": "on", f"allow_{ct_brain}": "on", f"price_{ct_brain}": "9000",
                      "stamp": (io.BytesIO(PNG), "s.png"), "signature": (io.BytesIO(PNG), "g.png")})
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:400])

        # المستشفى: حجز + إكمال + إرسال
        hos = self.login("h@t.test", "hospass123")
        r = self.post(hos, "/h/bookings/new", {"name": "محمد أحمد حسن", "phone": "07705550001", "age": "40", "sex": "ذكر", "action": "continue"})
        self.assertEqual(r.status_code, 302)
        with self.app.app_context():
            oid = db.q1("SELECT id FROM orders")["id"]
        r = self.post(hos, f"/h/exams/{oid}", {"exam_type_id": str(ct_brain), "device": "CT 64", "action": "send"})
        self.assertIn("ارفع صور", self.follow(hos, r))  # لا إرسال بدون صور
        r = self.post(hos, f"/h/exams/{oid}", {"exam_type_id": str(ct_brain), "device": "CT 64", "action": "send",
                      "images": (io.BytesIO(PNG), "a.png")})
        with self.app.app_context():
            o = db.q1("SELECT * FROM orders WHERE id=?", (oid,))
            self.assertEqual((o["status"], o["price"]), ("awaiting_doctor", 18000))
        self.assertEqual(hos.get(f"/h/bookings/{oid}/sheet").status_code, 200)

        # الطبيب
        doc = self.login("d@t.test", "docpass123")
        self.assertIn("ct".upper() if False else "مفراس", doc.get("/d/worklist").get_data(as_text=True))
        self.post(doc, f"/d/orders/{oid}/start")
        r = self.post(doc, f"/d/orders/{oid}/report", {"action": "import", "docx": (io.BytesIO(make_docx("لا توجد آفة")), "r.docx")})
        self.assertIn("لا توجد آفة", r.get_data(as_text=True))
        r = self.post(doc, f"/d/orders/{oid}/report", {"action": "sign", "body": "الموجودات: طبيعي"})
        self.assertEqual(r.status_code, 302)
        with self.app.app_context():
            o = db.q1("SELECT * FROM orders WHERE id=?", (oid,))
            self.assertEqual((o["status"], o["published"], o["doctor_fee"]), ("completed", 0, 9000))  # بانتظار موافقة المستشفى
            token, phone = o["token"], o["phone"]

        # المريض: لا يرى التقرير قبل الموافقة
        pat = self.client()
        self.assertIn("لم يصل التقرير", pat.get(f"/r/{token}").get_data(as_text=True))
        self.post(hos, f"/h/reports/{oid}/approve")
        self.assertIn("رمز التحقق", pat.get(f"/r/{token}").get_data(as_text=True))
        self.assertNotIn("الموجودات", pat.get(f"/r/{token}").get_data(as_text=True))
        r = self.post(pat, "/otp", {"token": token})
        code = re.search(r"رمز التحقق (\d{6})", r.get_data(as_text=True)).group(1)
        bad = self.post(pat, "/verify", {"code": "000000", "next": f"/r/{token}"})
        self.assertEqual(bad.status_code, 400)
        ok = self.post(pat, "/verify", {"code": code, "next": f"/r/{token}"})
        self.assertEqual(ok.status_code, 302)
        page = pat.get(f"/r/{token}").get_data(as_text=True)
        self.assertIn("الموجودات: طبيعي", page)
        with pat.session_transaction() as s:
            s["rv"] = {"phone": s["rv"]["phone"], "until": 0}  # انتهاء جلسة الـ10 دقائق
        self.assertNotIn("الموجودات: طبيعي", pat.get(f"/r/{token}").get_data(as_text=True))

        # مستشفى آخر لا يرى بيانات الأول
        adm_r = self.post(adm, "/admin/hospitals/new", {"name": "مركز ثانٍ", "email": "h2@t.test", "password": "hospass456", "active": "on"})
        h2 = self.login("h2@t.test", "hospass456")
        self.assertEqual(h2.get(f"/h/exams/{oid}").status_code, 404)

        # الجرد: تصفير جزئي ثم كلي
        fin = adm.get("/admin/finance").get_data(as_text=True)
        self.assertIn("18,000", fin)
        r = self.post(adm, "/admin/finance/settle", {"party_type": "hospital", "party_id": str(hid), "amount": "5000"})
        with self.app.app_context():
            self.assertEqual(self.app.test_request_context() and __import__("app.services", fromlist=["x"]).balance("hospital", hid), 13000)
        r = self.post(adm, "/admin/finance/settle", {"party_type": "hospital", "party_id": str(hid), "amount": "99999"}, follow_redirects=True)
        self.assertIn("أكبر من الرصيد", r.get_data(as_text=True))

    def follow(self, c, r):
        return c.get(r.headers["Location"]).get_data(as_text=True)

    def test_patient_direct_flow(self):
        with self.app.app_context():
            xr = db.q1("SELECT id FROM exam_types WHERE modality='xray' AND region='الصدر'")["id"]
        adm = self.login("admin@t.test", "adminpass1")
        self.post(adm, "/admin/doctors/new", {"name": "د. سارة", "email": "s@t.test", "password": "docpass123", "active": "on",
                  "accept_direct": "on", f"allow_{xr}": "on", f"price_{xr}": "2000"})
        p = self.client()
        r = self.post(p, "/order/new", {"name": "علي حسين", "age": "30", "sex": "ذكر", "phone": "07701234567", "exam_type_id": str(xr),
                      "request_sheet": (io.BytesIO(PNG), "req.png"), "images": (io.BytesIO(PNG), "x.png")})
        self.assertEqual(r.status_code, 400)  # الاسم غير ثلاثي
        r = self.post(p, "/order/new", {"name": "علي حسين كاظم", "age": "30", "sex": "ذكر", "phone": "+964 770 123 4567", "exam_type_id": str(xr),
                      "request_sheet": (io.BytesIO(PNG), "req.png"), "images": (io.BytesIO(PNG), "x.exe")})
        self.assertEqual(r.status_code, 400)  # امتداد غير مسموح
        r = self.post(p, "/order/new", {"name": "علي حسين كاظم", "age": "30", "sex": "ذكر", "phone": "+964 770 123 4567", "exam_type_id": str(xr),
                      "request_sheet": (io.BytesIO(PNG), "req.png"), "images": (io.BytesIO(PNG), "x.png")})
        self.assertEqual(r.status_code, 302)
        with self.app.app_context():
            o = db.q1("SELECT * FROM orders")
            self.assertEqual((o["phone"], o["status"], o["price"]), ("07701234567", "pending_payment", 5000))
        tok = o["token"]
        # لا إرسال قبل الدفع
        self.post(p, f"/order/{tok}/send")
        with self.app.app_context():
            self.assertEqual(db.q1("SELECT status FROM orders")["status"], "pending_payment")
        self.post(p, f"/order/{tok}/pay")
        self.post(p, f"/pay/mock/{tok}/confirm")
        self.post(p, f"/order/{tok}/send")
        self.assertEqual(p.get(f"/order/{tok}/sheet").status_code, 200)
        doc = self.login("s@t.test", "docpass123")
        self.assertIn(o["ref"], doc.get("/d/worklist").get_data(as_text=True))
        # لا يستطيع الوصول لملفات غير مصرح بها
        self.assertEqual(self.client().get("/files/1").status_code, 302)


if __name__ == "__main__":
    unittest.main()
