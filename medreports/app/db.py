import os
import sqlite3
from flask import current_app, g


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(_=None):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def q(sql, args=()):
    return get_db().execute(sql, args).fetchall()


def q1(sql, args=()):
    return get_db().execute(sql, args).fetchone()


def ex(sql, args=(), commit=True):
    cur = get_db().execute(sql, args)
    if commit:
        get_db().commit()
    return cur.lastrowid


def commit():
    get_db().commit()


def init_db():
    with open(os.path.join(os.path.dirname(__file__), "schema.sql"), encoding="utf-8") as f:
        get_db().executescript(f.read())
    get_db().execute("PRAGMA journal_mode=WAL")
    get_db().commit()
