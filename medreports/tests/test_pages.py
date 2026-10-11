"""يفتح كل صفحات القوائم لكل دور للتأكد أن القوالب كلها تعمل."""
import tempfile
import unittest

from app import NAV, create_app


class Pages(unittest.TestCase):
    def test_all_nav_pages_render(self):
        d = tempfile.mkdtemp()
        app = create_app({"TESTING": True, "DATABASE": d + "/t.db", "UPLOAD_DIR": d + "/u",
                          "BOOTSTRAP_ADMIN_EMAIL": "a@t.test", "BOOTSTRAP_ADMIN_PASSWORD": "adminpass1", "SECRET_KEY": "x"})
        runner = app.test_cli_runner()
        self.assertIn("hospital@demo.test", runner.invoke(args=["seed-demo"]).output)
        for role, email, pw in (("admin", "a@t.test", "adminpass1"), ("hospital", "hospital@demo.test", "demo12345"),
                                ("doctor", "doctor@demo.test", "demo12345")):
            c = app.test_client()
            with c.session_transaction() as s:
                s["csrf"] = "t"
            self.assertEqual(c.post("/login", data={"email": email, "password": pw, "csrf_token": "t"}).status_code, 302)
            urls = [c.application.url_map.bind("").build(ep) for ep, _ in NAV[role]]
            urls += ["/notifications", "/account"]
            if role == "admin":
                urls += ["/admin/hospitals/new", "/admin/doctors/new", "/admin/hospitals/1", "/admin/doctors/1"]
            if role == "hospital":
                urls += ["/h/bookings/new"]
            for u in urls:
                r = c.get(u)
                self.assertEqual(r.status_code, 200, f"{role} {u}")
        # صفحات عامة + 403 بين الأدوار
        c = app.test_client()
        with c.session_transaction() as s:
            s["csrf"] = "t"
        c.post("/login", data={"email": "doctor@demo.test", "password": "demo12345", "csrf_token": "t"})
        self.assertEqual(c.get("/admin/").status_code, 403)
        self.assertEqual(c.get("/h/").status_code, 403)


if __name__ == "__main__":
    unittest.main()
