import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "app.db")
BERLIN_TZ = ZoneInfo("Europe/Berlin")

load_dotenv()

app = Flask(__name__)


STEAM_ID_RE = re.compile(r"^\d{17}$")
PROFILE_ID_RE = re.compile(r"steamcommunity\.com/profiles/(\d{17})", re.IGNORECASE)
PROFILE_VANITY_RE = re.compile(r"steamcommunity\.com/id/([^/]+)", re.IGNORECASE)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            steamid64 TEXT UNIQUE,
            input_raw TEXT NOT NULL,
            persona_name TEXT,
            avatar_url TEXT,
            profile_url TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            cs2_level_note TEXT,
            created_at TEXT NOT NULL,
            last_fetched_at TEXT,
            info_warning INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            account_id INTEGER PRIMARY KEY,
            weekly_drop_done INTEGER NOT NULL DEFAULT 0,
            near_levelup_done INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def get_api_key():
    api_key = os.getenv("STEAM_API_KEY")
    if api_key:
        return api_key
    config_path = os.path.join(BASE_DIR, "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data.get("STEAM_API_KEY")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def resolve_vanity_url(vanity):
    api_key = get_api_key()
    if not api_key:
        return None
    try:
        response = requests.get(
            "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/",
            params={"key": api_key, "vanityurl": vanity},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("response", {}).get("success") == 1:
            return payload["response"].get("steamid")
    except requests.RequestException:
        return None
    except (ValueError, json.JSONDecodeError, KeyError):
        return None
    return None


def fetch_player_summary(steamid64):
    api_key = get_api_key()
    if not api_key:
        return None
    try:
        response = requests.get(
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
            params={"key": api_key, "steamids": steamid64},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        players = payload.get("response", {}).get("players", [])
        if not players:
            return None
        player = players[0]
        return {
            "persona_name": player.get("personaname"),
            "avatar_url": player.get("avatarfull"),
            "profile_url": player.get("profileurl"),
        }
    except requests.RequestException:
        return None
    except (ValueError, json.JSONDecodeError, KeyError):
        return None


def parse_input_to_steamid(value):
    if not value:
        return None, None
    value = value.strip()
    if STEAM_ID_RE.match(value):
        return value, None
    profile_match = PROFILE_ID_RE.search(value)
    if profile_match:
        return profile_match.group(1), None
    vanity_match = PROFILE_VANITY_RE.search(value)
    if vanity_match:
        return None, vanity_match.group(1)
    return None, None


def get_next_sort_order(conn):
    row = conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS max_order FROM accounts").fetchone()
    return int(row["max_order"]) + 1


def get_last_reset_at(conn):
    row = conn.execute("SELECT value FROM meta WHERE key = 'last_reset_at'").fetchone()
    if not row or not row["value"]:
        return None
    try:
        return datetime.fromisoformat(row["value"])
    except ValueError:
        return None


def set_last_reset_at(conn, dt_value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('last_reset_at', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (dt_value.isoformat(),),
    )


def next_wednesday_one(after_dt):
    days_ahead = (2 - after_dt.weekday()) % 7
    candidate = after_dt + timedelta(days=days_ahead)
    candidate = candidate.replace(hour=1, minute=0, second=0, microsecond=0)
    if candidate <= after_dt:
        candidate += timedelta(days=7)
    return candidate


def maybe_reset_weekly_drop():
    conn = get_db_connection()
    last_reset_at = get_last_reset_at(conn)
    now_utc = datetime.now(timezone.utc)
    now_berlin = now_utc.astimezone(BERLIN_TZ)

    if last_reset_at is None:
        set_last_reset_at(conn, now_utc)
        conn.commit()
        conn.close()
        return

    try:
        last_reset_utc = last_reset_at
        if last_reset_utc.tzinfo is None:
            last_reset_utc = last_reset_utc.replace(tzinfo=timezone.utc)
        last_reset_berlin = last_reset_utc.astimezone(BERLIN_TZ)
    except ValueError:
        last_reset_berlin = now_berlin

    next_reset = next_wednesday_one(last_reset_berlin)
    if now_berlin >= next_reset:
        conn.execute("UPDATE tasks SET weekly_drop_done = 0")
        set_last_reset_at(conn, now_utc)
        conn.commit()

    conn.close()


def build_display_name(account):
    return account["persona_name"] or account["steamid64"] or account["input_raw"]


def build_profile_url(account):
    if account["profile_url"]:
        return account["profile_url"]
    if account["steamid64"]:
        return f"https://steamcommunity.com/profiles/{account['steamid64']}"
    return None


@app.route("/")
def index():
    conn = get_db_connection()
    accounts = conn.execute(
        """
        SELECT accounts.*, tasks.weekly_drop_done, tasks.near_levelup_done
        FROM accounts
        JOIN tasks ON tasks.account_id = accounts.id
        ORDER BY accounts.sort_order ASC, accounts.created_at ASC
        """
    ).fetchall()
    conn.close()

    prepared = []
    for account in accounts:
        profile_url = build_profile_url(account)
        prepared.append({
            "id": account["id"],
            "steamid64": account["steamid64"],
            "input_raw": account["input_raw"],
            "persona_name": account["persona_name"],
            "avatar_url": account["avatar_url"],
            "profile_url": profile_url,
            "cs2_level_note": account["cs2_level_note"] or "",
            "weekly_drop_done": account["weekly_drop_done"],
            "near_levelup_done": account["near_levelup_done"],
            "info_warning": account["info_warning"],
        })

    return render_template("index.html", accounts=prepared)


@app.route("/add", methods=["POST"])
def add_account():
    input_raw = request.form.get("account_input", "").strip()
    if not input_raw:
        return redirect(url_for("index"))

    steamid64, vanity = parse_input_to_steamid(input_raw)
    info_warning = 0
    if vanity:
        resolved = resolve_vanity_url(vanity)
        if resolved:
            steamid64 = resolved
        else:
            info_warning = 1

    summary = None
    if steamid64:
        summary = fetch_player_summary(steamid64)
        if summary is None and get_api_key():
            info_warning = 1

    created_at = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection()
    sort_order = get_next_sort_order(conn)

    conn.execute(
        """
        INSERT INTO accounts (
            steamid64, input_raw, persona_name, avatar_url, profile_url,
            sort_order, cs2_level_note, created_at, last_fetched_at, info_warning
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            steamid64,
            input_raw,
            summary["persona_name"] if summary else None,
            summary["avatar_url"] if summary else None,
            summary["profile_url"] if summary else None,
            sort_order,
            None,
            created_at,
            datetime.now(timezone.utc).isoformat() if summary else None,
            info_warning,
        ),
    )
    account_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        "INSERT INTO tasks (account_id, weekly_drop_done, near_levelup_done) VALUES (?, 0, 0)",
        (account_id,),
    )
    conn.commit()
    conn.close()

    return redirect(url_for("index"))


@app.route("/toggle/<int:account_id>/<string:task_name>", methods=["POST"])
def toggle_task(account_id, task_name):
    if task_name not in {"weekly_drop_done", "near_levelup_done"}:
        return redirect(url_for("index"))
    conn = get_db_connection()
    current = conn.execute(
        f"SELECT {task_name} FROM tasks WHERE account_id = ?", (account_id,)
    ).fetchone()
    if current:
        new_value = 0 if current[task_name] else 1
        conn.execute(
            f"UPDATE tasks SET {task_name} = ? WHERE account_id = ?",
            (new_value, account_id),
        )
        conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/level/<int:account_id>", methods=["POST"])
def update_level(account_id):
    note = request.form.get("cs2_level_note", "").strip()
    conn = get_db_connection()
    conn.execute(
        "UPDATE accounts SET cs2_level_note = ? WHERE id = ?",
        (note, account_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/delete/<int:account_id>", methods=["POST"])
def delete_account(account_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM tasks WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/refresh/<int:account_id>", methods=["POST"])
def refresh_account(account_id):
    conn = get_db_connection()
    account = conn.execute(
        "SELECT steamid64 FROM accounts WHERE id = ?",
        (account_id,),
    ).fetchone()
    if not account or not account["steamid64"]:
        conn.execute("UPDATE accounts SET info_warning = 1 WHERE id = ?", (account_id,))
        conn.commit()
        conn.close()
        return redirect(url_for("index"))

    summary = fetch_player_summary(account["steamid64"])
    if summary:
        conn.execute(
            """
            UPDATE accounts
            SET persona_name = ?, avatar_url = ?, profile_url = ?, last_fetched_at = ?, info_warning = 0
            WHERE id = ?
            """,
            (
                summary["persona_name"],
                summary["avatar_url"],
                summary["profile_url"],
                datetime.now(timezone.utc).isoformat(),
                account_id,
            ),
        )
    else:
        conn.execute("UPDATE accounts SET info_warning = 1 WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/reorder", methods=["POST"])
def reorder_accounts():
    data = request.get_json(silent=True)
    if not data or "order" not in data:
        return jsonify({"status": "error", "message": "invalid payload"}), 400
    order = data.get("order", [])
    conn = get_db_connection()
    for index, account_id in enumerate(order, start=1):
        conn.execute(
            "UPDATE accounts SET sort_order = ? WHERE id = ?",
            (index, account_id),
        )
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    init_db()
    maybe_reset_weekly_drop()
    app.run(host="127.0.0.1", port=5000, debug=False)
