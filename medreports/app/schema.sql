CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, role TEXT NOT NULL CHECK(role IN('admin','hospital','doctor')),
  name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, phone TEXT, password_hash TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hospitals(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
  address TEXT, logo TEXT, receive_notes TEXT, tpl_header TEXT, tpl_body TEXT, tpl_footer TEXT,
  publish_mode TEXT NOT NULL DEFAULT 'auto' CHECK(publish_mode IN('auto','hospital_approval')));
CREATE TABLE IF NOT EXISTS doctors(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
  specialty TEXT, stamp TEXT, signature TEXT, accept_direct INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS exam_types(
  id INTEGER PRIMARY KEY, modality TEXT NOT NULL, region TEXT NOT NULL,
  base_price INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS hospital_prices(
  hospital_id INTEGER NOT NULL REFERENCES hospitals(id), exam_type_id INTEGER NOT NULL REFERENCES exam_types(id),
  price INTEGER NOT NULL, PRIMARY KEY(hospital_id, exam_type_id));
CREATE TABLE IF NOT EXISTS doctor_prices(
  doctor_id INTEGER NOT NULL REFERENCES doctors(id), exam_type_id INTEGER NOT NULL REFERENCES exam_types(id),
  price INTEGER NOT NULL, PRIMARY KEY(doctor_id, exam_type_id));
CREATE TABLE IF NOT EXISTS doctor_hospitals(
  doctor_id INTEGER NOT NULL REFERENCES doctors(id), hospital_id INTEGER NOT NULL REFERENCES hospitals(id),
  PRIMARY KEY(doctor_id, hospital_id));
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, token TEXT UNIQUE NOT NULL,
  source TEXT NOT NULL CHECK(source IN('patient','hospital')),
  hospital_id INTEGER REFERENCES hospitals(id),
  patient_name TEXT NOT NULL, age INTEGER, sex TEXT, phone TEXT NOT NULL,
  exam_type_id INTEGER REFERENCES exam_types(id), exam_name TEXT,
  device TEXT, protocol TEXT, contrast TEXT, clinical_info TEXT, referring_doctor TEXT,
  status TEXT NOT NULL, price INTEGER NOT NULL DEFAULT 0,
  doctor_id INTEGER REFERENCES doctors(id), doctor_fee INTEGER NOT NULL DEFAULT 0,
  draft TEXT, published INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, sent_at TEXT, completed_at TEXT, delivered_at TEXT);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS ix_orders_hospital ON orders(hospital_id);
CREATE INDEX IF NOT EXISTS ix_orders_doctor ON orders(doctor_id);
CREATE INDEX IF NOT EXISTS ix_orders_phone ON orders(phone);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
  kind TEXT NOT NULL CHECK(kind IN('request_sheet','image','previous')),
  stored_name TEXT NOT NULL, original_name TEXT, size INTEGER, uploaded_by INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), version INTEGER NOT NULL,
  body TEXT NOT NULL, doctor_id INTEGER NOT NULL REFERENCES doctors(id), reason TEXT, created_at TEXT NOT NULL,
  UNIQUE(order_id, version));
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), amount INTEGER NOT NULL,
  provider TEXT NOT NULL, provider_ref TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settlements(
  id INTEGER PRIMARY KEY, party_type TEXT NOT NULL CHECK(party_type IN('hospital','doctor')),
  party_id INTEGER NOT NULL, amount INTEGER NOT NULL, note TEXT, created_by INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, user_id INTEGER, role TEXT, action TEXT NOT NULL, entity TEXT, entity_id INTEGER,
  detail TEXT, ip TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), text TEXT NOT NULL, link TEXT,
  is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS otps(
  phone TEXT PRIMARY KEY, code_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
