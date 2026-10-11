import click
from . import db
from .security import create_user


def register(app):
    @app.cli.command("create-admin")
    @click.argument("email")
    @click.argument("password")
    def create_admin(email, password):
        """إنشاء حساب إدارة إضافي."""
        create_user("admin", "مدير", email, "", password)
        click.echo("تم.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """بيانات تجريبية: مستشفى + طبيب (كلمة السر demo12345)."""
        if db.q1("SELECT 1 FROM users WHERE email='hospital@demo.test'"):
            click.echo("البيانات التجريبية موجودة مسبقاً.")
            return
        uh = create_user("hospital", "مستشفى الأمل التخصصي", "hospital@demo.test", "07701234567", "demo12345", commit=False)
        hid = db.ex("INSERT INTO hospitals(user_id,address,receive_notes,tpl_header,tpl_body,tpl_footer) VALUES (?,?,?,?,?,?)",
                    (uh, "بغداد - الكرادة", "استلم نتيجتك عبر مسح الرمز أو من صفحة النتائج برقم موبايلك.",
                     "قسم الأشعة والتصوير الطبي", "التقنية:\n\nالموجودات:\n\nالخلاصة:\n", "التقرير لا يُغني عن الفحص السريري."), commit=False)
        ud = create_user("doctor", "د. أحمد علي", "doctor@demo.test", "07709876543", "demo12345", commit=False)
        did = db.ex("INSERT INTO doctors(user_id,specialty,accept_direct) VALUES (?,?,1)", (ud, "أشعة تشخيصية"), commit=False)
        db.ex("INSERT INTO doctor_hospitals(doctor_id,hospital_id) VALUES (?,?)", (did, hid), commit=False)
        for e in db.q("SELECT id, base_price FROM exam_types"):
            db.ex("INSERT INTO doctor_prices(doctor_id,exam_type_id,price) VALUES (?,?,?)", (did, e["id"], e["base_price"] // 2), commit=False)
        db.commit()
        click.echo("hospital@demo.test / doctor@demo.test  (demo12345)")
