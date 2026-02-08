import os
import json
import random
import secrets
import datetime
import urllib.request
import psycopg2
import psycopg2.extras
import psycopg2.errors
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, g, send_file, abort,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from game import NameGrid, export_grid_to_pdf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
DATABASE_URL = os.environ.get("DATABASE_URL")
SUPER_ADMIN_PASSWORD = os.environ.get("SUPER_ADMIN_PASSWORD", "admin1234")
SUPERADMIN_DISCORD_WEBHOOK = os.environ.get("SUPERADMIN_DISCORD_WEBHOOK", "")
HOST_ACCESS_CODE = os.environ.get("HOST_ACCESS_CODE", "")
BROWSE_ACCESS_CODE = os.environ.get("BROWSE_ACCESS_CODE", "")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(16))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PREFERRED_URL_SCHEME"] = "https"
csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[])

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS games (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    admin_password_hash TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    row_numbers TEXT NOT NULL DEFAULT '[]',
    col_numbers TEXT NOT NULL DEFAULT '[]',
    is_complete INTEGER NOT NULL DEFAULT 0,
    numbers_released INTEGER NOT NULL DEFAULT 0,
    team_x      TEXT NOT NULL DEFAULT '',
    team_y      TEXT NOT NULL DEFAULT '',
    payment_methods TEXT NOT NULL DEFAULT '[]',
    is_locked   INTEGER NOT NULL DEFAULT 0,
    lock_at     TEXT NOT NULL DEFAULT '',
    square_price TEXT NOT NULL DEFAULT '',
    payout_info TEXT NOT NULL DEFAULT '',
    max_claims  INTEGER NOT NULL DEFAULT 0,
    discord_webhook TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS claims (
    id          SERIAL PRIMARY KEY,
    game_id     TEXT NOT NULL,
    "row"       INTEGER NOT NULL,
    col         INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    claimed_at  TEXT NOT NULL,
    FOREIGN KEY (game_id) REFERENCES games(id),
    UNIQUE(game_id, "row", col)
);

CREATE TABLE IF NOT EXISTS players (
    id          SERIAL PRIMARY KEY,
    game_id     TEXT NOT NULL,
    player_name TEXT NOT NULL,
    phone       TEXT NOT NULL DEFAULT '',
    joined_at   TEXT NOT NULL,
    is_banned   INTEGER NOT NULL DEFAULT 0,
    bonus_claims INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (game_id) REFERENCES games(id),
    UNIQUE(game_id, player_name)
);

CREATE TABLE IF NOT EXISTS square_requests (
    id           SERIAL PRIMARY KEY,
    game_id      TEXT NOT NULL,
    player_name  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT NOT NULL,
    FOREIGN KEY (game_id) REFERENCES games(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id           SERIAL PRIMARY KEY,
    game_id      TEXT NOT NULL,
    player_name  TEXT NOT NULL,
    message      TEXT NOT NULL,
    sent_at      TEXT NOT NULL,
    sender_type  TEXT NOT NULL DEFAULT 'player',
    FOREIGN KEY (game_id) REFERENCES games(id)
);
"""

MIGRATIONS = [
    "ALTER TABLE games ADD COLUMN numbers_released INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS players (
        id          SERIAL PRIMARY KEY,
        game_id     TEXT NOT NULL,
        player_name TEXT NOT NULL,
        phone       TEXT NOT NULL DEFAULT '',
        joined_at   TEXT NOT NULL,
        is_banned   INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (game_id) REFERENCES games(id),
        UNIQUE(game_id, player_name)
    )""",
    "ALTER TABLE players ADD COLUMN phone TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE games ADD COLUMN team_x TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE games ADD COLUMN team_y TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE games ADD COLUMN payment_methods TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE games ADD COLUMN is_locked INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE games ADD COLUMN lock_at TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE games ADD COLUMN square_price TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE games ADD COLUMN payout_info TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE games ADD COLUMN max_claims INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE players ADD COLUMN bonus_claims INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS square_requests (
        id           SERIAL PRIMARY KEY,
        game_id      TEXT NOT NULL,
        player_name  TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'pending',
        requested_at TEXT NOT NULL,
        FOREIGN KEY (game_id) REFERENCES games(id)
    )""",
    "ALTER TABLE games ADD COLUMN discord_webhook TEXT NOT NULL DEFAULT ''",
    """CREATE TABLE IF NOT EXISTS messages (
        id           SERIAL PRIMARY KEY,
        game_id      TEXT NOT NULL,
        player_name  TEXT NOT NULL,
        message      TEXT NOT NULL,
        sent_at      TEXT NOT NULL,
        FOREIGN KEY (game_id) REFERENCES games(id)
    )""",
    "ALTER TABLE messages ADD COLUMN sender_type TEXT NOT NULL DEFAULT 'player'",
]


# ── Database helpers ──────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL)
        g.db.autocommit = False
    return g.db


def get_cursor():
    return get_db().cursor(cursor_factory=psycopg2.extras.RealDictCursor)


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = psycopg2.connect(DATABASE_URL)
    cur = db.cursor()
    cur.execute(SCHEMA_SQL)
    db.commit()
    for migration in MIGRATIONS:
        try:
            cur.execute(migration)
            db.commit()
        except Exception:
            db.rollback()
    cur.close()
    db.close()


# ── Discord webhook ───────────────────────────────────────────────

DISCORD_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
)


def send_discord_notification(webhook_url, message):
    if not webhook_url:
        return
    if not webhook_url.startswith(DISCORD_WEBHOOK_PREFIXES):
        return
    try:
        data = json.dumps({"content": message}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url, data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "NumFootGrid/1.0",
            },
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


# ── Grid helpers ──────────────────────────────────────────────────

def build_grid_from_db(game_id):
    cur = get_cursor()
    cur.execute("SELECT * FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game:
        return None, None

    grid = NameGrid()
    col_numbers = json.loads(game["col_numbers"])
    row_numbers = json.loads(game["row_numbers"])
    if col_numbers and row_numbers:
        for i, num in enumerate(col_numbers):
            grid.grid[0][i + 1] = str(num)
        for i, num in enumerate(row_numbers):
            grid.grid[i + 1][0] = str(num)
        grid.numbers_generated = True

    cur.execute(
        'SELECT "row", col, player_name FROM claims WHERE game_id = %s',
        (game_id,),
    )
    claims = cur.fetchall()
    for claim in claims:
        grid.grid[claim["row"]][claim["col"]] = claim["player_name"]

    return grid, game


def get_claim_count(game_id):
    cur = get_cursor()
    cur.execute(
        "SELECT COUNT(*) as cnt FROM claims WHERE game_id = %s", (game_id,)
    )
    row = cur.fetchone()
    return row["cnt"]


def get_player_count(game_id):
    cur = get_cursor()
    cur.execute(
        "SELECT COUNT(*) as cnt FROM players WHERE game_id = %s AND is_banned = 0",
        (game_id,),
    )
    row = cur.fetchone()
    return row["cnt"]


def is_admin(game_id):
    return game_id in session.get("admin_games", []) or is_superadmin()


def is_superadmin():
    return session.get("is_superadmin", False)


def is_game_locked(game):
    if game["is_locked"]:
        return True
    lock_at = game["lock_at"]
    if lock_at:
        try:
            return datetime.datetime.now() >= datetime.datetime.fromisoformat(lock_at)
        except ValueError:
            return False
    return False


def generate_and_store_numbers(game_id):
    db = get_db()
    cur = get_cursor()
    cur.execute("SELECT row_numbers, col_numbers FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    existing_row = json.loads(game["row_numbers"])
    existing_col = json.loads(game["col_numbers"])
    if existing_row and existing_col:
        return existing_row, existing_col

    row_numbers = [secrets.randbelow(10) for _ in range(10)]
    col_numbers = [secrets.randbelow(10) for _ in range(10)]
    cur.execute(
        "UPDATE games SET row_numbers = %s, col_numbers = %s WHERE id = %s",
        (json.dumps(row_numbers), json.dumps(col_numbers), game_id),
    )
    db.commit()
    return row_numbers, col_numbers


# ── Routes: Public ────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/games", methods=["GET", "POST"])
def browse_games():
    if BROWSE_ACCESS_CODE and not session.get("browse_verified"):
        if request.method == "POST" and "access_code" in request.form:
            if request.form["access_code"].strip() == BROWSE_ACCESS_CODE:
                session["browse_verified"] = True
                return redirect(url_for("browse_games"))
            else:
                flash("Invalid access code.", "error")
        return render_template("browse_gate.html")

    cur = get_cursor()
    cur.execute("SELECT * FROM games ORDER BY created_at DESC")
    all_games = cur.fetchall()

    player_names = session.get("player_names", {})
    games = []
    for game in all_games:
        locked = is_game_locked(game)
        already_joined = game["id"] in player_names
        games.append({
            "id": game["id"],
            "name": game["name"],
            "team_x": game["team_x"],
            "team_y": game["team_y"],
            "claim_count": get_claim_count(game["id"]),
            "square_price": game["square_price"],
            "locked": locked,
            "numbers_released": game["numbers_released"],
            "already_joined": already_joined,
        })

    return render_template("browse_games.html", games=games)


@app.route("/create", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def create_game():
    if HOST_ACCESS_CODE and not session.get("host_verified"):
        if request.method == "POST" and "access_code" in request.form:
            if request.form["access_code"].strip() == HOST_ACCESS_CODE:
                session["host_verified"] = True
                return redirect(url_for("create_game"))
            else:
                flash("Invalid access code.", "error")
        return render_template("host_gate.html")

    if request.method == "GET":
        return render_template("admin_create.html")

    name = request.form.get("name", "").strip()
    password = request.form.get("password", "").strip()
    confirm = request.form.get("confirm", "").strip()
    team_x = request.form.get("team_x", "").strip()
    team_y = request.form.get("team_y", "").strip()

    if not name:
        flash("Game name is required.", "error")
        return redirect(url_for("create_game"))
    if not team_x or not team_y:
        flash("Both team names are required.", "error")
        return redirect(url_for("create_game"))
    if len(password) < 4:
        flash("Password must be at least 4 characters.", "error")
        return redirect(url_for("create_game"))
    if password != confirm:
        flash("Passwords do not match.", "error")
        return redirect(url_for("create_game"))

    payment_methods = []
    pay_count = request.form.get("payment_count", 0, type=int)
    for i in range(pay_count):
        label = request.form.get(f"pay_label_{i}", "").strip()
        user = request.form.get(f"pay_user_{i}", "").strip()
        if label and user:
            payment_methods.append({"label": label, "username": user})

    square_price = request.form.get("square_price", "").strip()
    payout_info = request.form.get("payout_info", "").strip()
    lock_at = request.form.get("lock_at", "").strip()
    max_claims = request.form.get("max_claims", 0, type=int)
    discord_webhook = request.form.get("discord_webhook", "").strip()
    if discord_webhook and not discord_webhook.startswith(DISCORD_WEBHOOK_PREFIXES):
        flash("Discord webhook must be a valid Discord webhook URL.", "error")
        return redirect(url_for("create_game"))

    custom_code = request.form.get("custom_code", "").strip().upper()
    if custom_code:
        if len(custom_code) != 6 or not custom_code.isalnum():
            flash("Game code must be exactly 6 alphanumeric characters.", "error")
            return redirect(url_for("create_game"))
        game_id = custom_code
    else:
        game_id = secrets.token_hex(3).upper()  # 6 hex chars

    now = datetime.datetime.now().isoformat()

    db = get_db()
    cur = get_cursor()
    cur.execute("SELECT 1 FROM games WHERE id = %s", (game_id,))
    if cur.fetchone():
        flash("That game code is already taken. Try a different one.", "error")
        return redirect(url_for("create_game"))
    cur.execute(
        "INSERT INTO games (id, name, admin_password_hash, created_at, row_numbers, col_numbers, team_x, team_y, payment_methods, square_price, payout_info, lock_at, max_claims, discord_webhook) "
        "VALUES (%s, %s, %s, %s, '[]', '[]', %s, %s, %s, %s, %s, %s, %s, %s)",
        (game_id, name, generate_password_hash(password), now, team_x, team_y, json.dumps(payment_methods), square_price, payout_info, lock_at, max_claims, discord_webhook),
    )
    db.commit()

    send_discord_notification(SUPERADMIN_DISCORD_WEBHOOK, f"New game '{name}' created (ID: {game_id})")

    admin_games = session.get("admin_games", [])
    admin_games.append(game_id)
    session["admin_games"] = admin_games

    flash(f"Game '{name}' created!", "success")
    return redirect(url_for("admin_panel", game_id=game_id))


# ── Routes: Player ────────────────────────────────────────────────

@app.route("/my-games")
def my_games():
    player_names = session.get("player_names", {})
    if not player_names:
        return render_template("my_games.html", games=[])

    cur = get_cursor()
    games = []
    for gid, pname in player_names.items():
        cur.execute("SELECT * FROM games WHERE id = %s", (gid,))
        game = cur.fetchone()
        if game:
            cur.execute(
                "SELECT is_banned FROM players WHERE game_id = %s AND player_name = %s",
                (gid, pname),
            )
            player = cur.fetchone()
            if player and player["is_banned"]:
                continue
            claim_count = get_claim_count(gid)
            games.append({
                "id": game["id"],
                "name": game["name"],
                "player_name": pname,
                "claim_count": claim_count,
                "numbers_released": game["numbers_released"],
                "locked": is_game_locked(game),
            })

    return render_template("my_games.html", games=games)


@app.route("/recover", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def recover():
    if request.method == "GET":
        return render_template("recover.html")

    name = request.form.get("name", "").strip()
    last4 = request.form.get("last4", "").strip()

    if not name or not last4 or len(last4) != 4 or not last4.isdigit():
        flash("Enter your name and the last 4 digits of your phone.", "error")
        return redirect(url_for("recover"))

    cur = get_cursor()
    cur.execute(
        "SELECT game_id, player_name, phone FROM players WHERE player_name = %s AND is_banned = 0",
        (name,),
    )
    rows = cur.fetchall()

    matched = []
    for row in rows:
        stored_digits = "".join(c for c in row["phone"] if c.isdigit())[-4:]
        if stored_digits == last4:
            matched.append(row["game_id"])

    if not matched:
        flash("No games found for that name and phone number.", "error")
        return redirect(url_for("recover"))

    player_names = session.get("player_names", {})
    for gid in matched:
        player_names[gid] = name
    session["player_names"] = player_names

    flash(f"Recovered {len(matched)} game{'s' if len(matched) != 1 else ''}!", "success")
    return redirect(url_for("my_games"))


@app.route("/game/<game_id>")
def game_view(game_id):
    player_names = session.get("player_names", {})
    if game_id not in player_names:
        return redirect(url_for("join_game", game_id=game_id))

    cur = get_cursor()
    pname = player_names[game_id]
    cur.execute(
        "SELECT is_banned, bonus_claims FROM players WHERE game_id = %s AND player_name = %s",
        (game_id, pname),
    )
    player = cur.fetchone()
    if player and player["is_banned"]:
        flash("You have been removed from this game.", "error")
        del player_names[game_id]
        session["player_names"] = player_names
        return redirect(url_for("index"))

    grid, game = build_grid_from_db(game_id)
    if not grid:
        abort(404)

    claim_count = get_claim_count(game_id)
    col_numbers = json.loads(game["col_numbers"])
    row_numbers = json.loads(game["row_numbers"])

    payment_methods = json.loads(game["payment_methods"]) if game["payment_methods"] else []
    locked = is_game_locked(game)

    at_limit = False
    has_pending_request = False
    max_claims = game["max_claims"]
    if max_claims > 0:
        bonus = player["bonus_claims"] if player else 0
        allowed = max_claims + bonus
        cur.execute(
            "SELECT COUNT(*) as cnt FROM claims WHERE game_id = %s AND player_name = %s",
            (game_id, pname),
        )
        my_claims = cur.fetchone()["cnt"]
        at_limit = my_claims >= allowed

    if at_limit:
        cur.execute(
            "SELECT id FROM square_requests WHERE game_id = %s AND player_name = %s AND status = 'pending'",
            (game_id, pname),
        )
        pending = cur.fetchone()
        has_pending_request = pending is not None

    cur.execute(
        "SELECT message, sent_at, sender_type FROM messages WHERE game_id = %s AND player_name = %s ORDER BY id ASC",
        (game_id, pname),
    )
    chat_messages = cur.fetchall()

    return render_template(
        "game_grid.html",
        game=game,
        game_id=game_id,
        grid=grid.grid,
        col_numbers=col_numbers,
        row_numbers=row_numbers,
        claim_count=claim_count,
        player_name=pname,
        payment_methods=payment_methods,
        locked=locked,
        at_limit=at_limit,
        has_pending_request=has_pending_request,
        chat_messages=chat_messages,
    )


@app.route("/game/<game_id>/join", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def join_game(game_id):
    db = get_db()
    cur = get_cursor()
    cur.execute("SELECT * FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game:
        abort(404)

    if is_game_locked(game):
        return render_template("join_game.html", game_id=game_id, game_name=game["name"], locked=True)

    if request.method == "POST":
        name = request.form.get("player_name", "").strip()
        phone = request.form.get("phone", "").strip()
        if not name:
            flash("Please enter your name.", "error")
            return redirect(url_for("join_game", game_id=game_id))
        if name.upper() == "VOID":
            flash("That name is not allowed.", "error")
            return redirect(url_for("join_game", game_id=game_id))
        if len(name) > 20:
            flash("Name must be 20 characters or less.", "error")
            return redirect(url_for("join_game", game_id=game_id))
        if not phone:
            flash("Please enter your phone number.", "error")
            return redirect(url_for("join_game", game_id=game_id))

        phone_digits = "".join(c for c in phone if c.isdigit())
        last4 = phone_digits[-4:] if len(phone_digits) >= 4 else phone_digits

        cur.execute(
            "SELECT phone, is_banned FROM players WHERE game_id = %s AND player_name = %s",
            (game_id, name),
        )
        existing = cur.fetchone()

        if existing:
            if existing["is_banned"]:
                flash("You have been removed from this game.", "error")
                return redirect(url_for("index"))

            existing_digits = "".join(c for c in existing["phone"] if c.isdigit())[-4:]
            if last4 and existing_digits and last4 == existing_digits:
                # Same person rejoining (last 4 digits match)
                player_names = session.get("player_names", {})
                player_names[game_id] = name
                session["player_names"] = player_names
                return redirect(url_for("game_view", game_id=game_id))
            else:
                # Different person, same name — append last 4 digits
                name = f"{name} ({last4})"
                cur.execute(
                    "SELECT is_banned FROM players WHERE game_id = %s AND player_name = %s",
                    (game_id, name),
                )
                also_exists = cur.fetchone()
                if also_exists:
                    if also_exists["is_banned"]:
                        flash("You have been removed from this game.", "error")
                        return redirect(url_for("index"))
                    # Same modified name already exists — same person rejoining
                    player_names = session.get("player_names", {})
                    player_names[game_id] = name
                    session["player_names"] = player_names
                    return redirect(url_for("game_view", game_id=game_id))

        now = datetime.datetime.now().isoformat()
        try:
            cur.execute(
                "INSERT INTO players (game_id, player_name, phone, joined_at) VALUES (%s, %s, %s, %s)",
                (game_id, name, phone, now),
            )
            db.commit()
            send_discord_notification(game["discord_webhook"], f"Player '{name}' joined game '{game['name']}'")
        except psycopg2.errors.UniqueViolation:
            db.rollback()
            flash("That name is already taken. Please use a different name.", "error")
            return redirect(url_for("join_game", game_id=game_id))

        player_names = session.get("player_names", {})
        player_names[game_id] = name
        session["player_names"] = player_names
        return redirect(url_for("game_view", game_id=game_id))

    return render_template("join_game.html", game_id=game_id, game_name=game["name"], locked=False)


@app.route("/game/<game_id>/claim", methods=["POST"])
@limiter.limit("20 per minute")
def claim_spot(game_id):
    player_names = session.get("player_names", {})
    if game_id not in player_names:
        return redirect(url_for("join_game", game_id=game_id))

    db = get_db()
    cur = get_cursor()
    cur.execute("SELECT * FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game:
        abort(404)
    if is_game_locked(game):
        flash("This game is locked. No more spots can be claimed.", "error")
        return redirect(url_for("game_view", game_id=game_id))

    row = request.form.get("row", type=int)
    col = request.form.get("col", type=int)
    if row is None or col is None or not (1 <= row <= 10 and 1 <= col <= 10):
        flash("Invalid spot.", "error")
        return redirect(url_for("game_view", game_id=game_id))

    cur.execute(
        "SELECT is_banned, bonus_claims FROM players WHERE game_id = %s AND player_name = %s",
        (game_id, player_names[game_id]),
    )
    player = cur.fetchone()
    if player and player["is_banned"]:
        flash("You have been removed from this game.", "error")
        return redirect(url_for("index"))

    max_claims = game["max_claims"]
    if max_claims > 0:
        bonus = player["bonus_claims"] if player else 0
        allowed = max_claims + bonus
        cur.execute(
            "SELECT COUNT(*) as cnt FROM claims WHERE game_id = %s AND player_name = %s",
            (game_id, player_names[game_id]),
        )
        my_claims = cur.fetchone()["cnt"]
        if my_claims >= allowed:
            flash(f"You've reached your limit of {allowed} squares.", "error")
            return redirect(url_for("game_view", game_id=game_id))

    now = datetime.datetime.now().isoformat()
    try:
        cur.execute(
            'INSERT INTO claims (game_id, "row", col, player_name, claimed_at) VALUES (%s, %s, %s, %s, %s)',
            (game_id, row, col, player_names[game_id], now),
        )
        db.commit()
        flash(f"You claimed row {row}, col {col}!", "success")
        send_discord_notification(game["discord_webhook"], f"'{player_names[game_id]}' claimed row {row}, col {col} in '{game['name']}'")
    except psycopg2.errors.UniqueViolation:
        db.rollback()
        flash("That spot was already taken! Pick another.", "error")

    count = get_claim_count(game_id)
    if count >= 100:
        generate_and_store_numbers(game_id)
        cur.execute("UPDATE games SET is_complete = 1 WHERE id = %s", (game_id,))
        db.commit()
        send_discord_notification(game["discord_webhook"], f"Grid is FULL for '{game['name']}'! All 100 squares claimed.")
        send_discord_notification(SUPERADMIN_DISCORD_WEBHOOK, f"Grid is FULL for '{game['name']}' (ID: {game_id})")

    return redirect(url_for("game_view", game_id=game_id))


@app.route("/game/<game_id>/pdf")
def player_pdf(game_id):
    player_names = session.get("player_names", {})
    if game_id not in player_names:
        abort(403)

    cur = get_cursor()
    cur.execute("SELECT * FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game:
        abort(404)
    if not game["numbers_released"]:
        flash("Numbers have not been released yet.", "error")
        return redirect(url_for("game_view", game_id=game_id))

    grid, _ = build_grid_from_db(game_id)
    if not grid:
        abort(404)

    pdf_path = export_grid_to_pdf(grid)
    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=f"grid_{game_id}.pdf",
    )


@app.route("/game/<game_id>/message-host", methods=["POST"])
@limiter.limit("3 per minute")
def message_host(game_id):
    player_names = session.get("player_names", {})
    if game_id not in player_names:
        return redirect(url_for("join_game", game_id=game_id))

    pname = player_names[game_id]
    message = request.form.get("message", "").strip()
    if not message:
        flash("Please enter a message.", "error")
        return redirect(url_for("game_view", game_id=game_id))
    if len(message) > 500:
        flash("Message must be 500 characters or less.", "error")
        return redirect(url_for("game_view", game_id=game_id))

    db = get_db()
    cur = get_cursor()
    cur.execute("SELECT name, discord_webhook FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game:
        abort(404)

    now = datetime.datetime.now().isoformat()
    cur.execute(
        "INSERT INTO messages (game_id, player_name, message, sent_at, sender_type) VALUES (%s, %s, %s, %s, 'player')",
        (game_id, pname, message, now),
    )
    db.commit()

    send_discord_notification(
        game["discord_webhook"],
        f"Message from '{pname}' in '{game['name']}':\n> {message}",
    )
    flash("Message sent!", "success")
    return redirect(url_for("game_view", game_id=game_id))


@app.route("/game/<game_id>/request-squares", methods=["POST"])
def request_squares(game_id):
    player_names = session.get("player_names", {})
    if game_id not in player_names:
        return redirect(url_for("join_game", game_id=game_id))

    db = get_db()
    cur = get_cursor()
    pname = player_names[game_id]

    cur.execute(
        "SELECT id FROM square_requests WHERE game_id = %s AND player_name = %s AND status = 'pending'",
        (game_id, pname),
    )
    existing = cur.fetchone()
    if existing:
        flash("You already have a pending request.", "error")
        return redirect(url_for("game_view", game_id=game_id))

    cur.execute("SELECT name, discord_webhook FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()

    now = datetime.datetime.now().isoformat()
    cur.execute(
        "INSERT INTO square_requests (game_id, player_name, status, requested_at) VALUES (%s, %s, 'pending', %s)",
        (game_id, pname, now),
    )
    db.commit()
    if game:
        send_discord_notification(game["discord_webhook"], f"'{pname}' requested more squares in '{game['name']}'")
    flash("Request sent to the host for more squares!", "success")
    return redirect(url_for("game_view", game_id=game_id))


# ── Routes: Admin ─────────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def admin_login():
    if request.method == "GET":
        return render_template("admin_recover.html")

    game_id = request.form.get("game_id", "").strip().upper()
    password = request.form.get("password", "").strip()

    if not game_id or not password:
        flash("Game ID and password are required.", "error")
        return redirect(url_for("admin_login"))

    cur = get_cursor()
    cur.execute("SELECT admin_password_hash FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game or not check_password_hash(game["admin_password_hash"], password):
        flash("Invalid Game ID or password.", "error")
        return redirect(url_for("admin_login"))

    admin_games = session.get("admin_games", [])
    if game_id not in admin_games:
        admin_games.append(game_id)
    session["admin_games"] = admin_games

    flash("Logged in as host!", "success")
    return redirect(url_for("admin_panel", game_id=game_id))


@app.route("/admin")
def admin_dashboard():
    admin_games = session.get("admin_games", [])
    if not admin_games:
        return render_template("admin_dashboard.html", games=[])

    cur = get_cursor()
    games = []
    for gid in admin_games:
        cur.execute("SELECT * FROM games WHERE id = %s", (gid,))
        game = cur.fetchone()
        if game:
            games.append({
                "id": game["id"],
                "name": game["name"],
                "created_at": game["created_at"],
                "is_complete": game["is_complete"],
                "numbers_released": game["numbers_released"],
                "claim_count": get_claim_count(gid),
                "player_count": get_player_count(gid),
                "locked": is_game_locked(game),
            })

    return render_template("admin_dashboard.html", games=games)


@app.route("/admin/<game_id>", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def admin_panel(game_id):
    cur = get_cursor()
    cur.execute("SELECT * FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game:
        abort(404)

    if not is_admin(game_id):
        if request.method == "POST" and "admin_password" in request.form:
            if check_password_hash(game["admin_password_hash"], request.form["admin_password"]):
                admin_games = session.get("admin_games", [])
                admin_games.append(game_id)
                session["admin_games"] = admin_games
                return redirect(url_for("admin_panel", game_id=game_id))
            else:
                flash("Wrong password.", "error")
        return render_template("admin_login.html", game_id=game_id, game_name=game["name"])

    grid, _ = build_grid_from_db(game_id)
    claim_count = get_claim_count(game_id)
    player_count = get_player_count(game_id)
    col_numbers = json.loads(game["col_numbers"])
    row_numbers = json.loads(game["row_numbers"])
    has_numbers = bool(col_numbers and row_numbers)

    player_url = request.url_root.rstrip("/") + url_for("game_view", game_id=game_id)
    locked = is_game_locked(game)

    cur.execute(
        "SELECT COUNT(*) as cnt FROM square_requests WHERE game_id = %s AND status = 'pending'",
        (game_id,),
    )
    pending_request_count = cur.fetchone()["cnt"]

    cur.execute(
        "SELECT player_name, message, sent_at, sender_type FROM messages WHERE game_id = %s ORDER BY id ASC",
        (game_id,),
    )
    all_messages = cur.fetchall()
    chat_threads = {}
    for msg in all_messages:
        pname = msg["player_name"]
        if pname not in chat_threads:
            chat_threads[pname] = []
        chat_threads[pname].append(msg)

    return render_template(
        "admin_panel.html",
        game=game,
        game_id=game_id,
        grid=grid.grid,
        col_numbers=col_numbers,
        row_numbers=row_numbers,
        claim_count=claim_count,
        player_count=player_count,
        player_url=player_url,
        has_numbers=has_numbers,
        locked=locked,
        pending_request_count=pending_request_count,
        chat_threads=chat_threads,
    )


@app.route("/admin/<game_id>/players")
def admin_players(game_id):
    if not is_admin(game_id):
        abort(403)

    cur = get_cursor()
    cur.execute("SELECT * FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game:
        abort(404)

    cur.execute(
        "SELECT * FROM players WHERE game_id = %s ORDER BY joined_at DESC",
        (game_id,),
    )
    players = cur.fetchall()

    player_claims = {}
    cur.execute(
        "SELECT player_name, COUNT(*) as cnt FROM claims WHERE game_id = %s GROUP BY player_name",
        (game_id,),
    )
    claims = cur.fetchall()
    for c in claims:
        player_claims[c["player_name"]] = c["cnt"]

    pending_requests = set()
    cur.execute(
        "SELECT player_name FROM square_requests WHERE game_id = %s AND status = 'pending'",
        (game_id,),
    )
    reqs = cur.fetchall()
    for r in reqs:
        pending_requests.add(r["player_name"])

    return render_template(
        "admin_players.html",
        game=game,
        game_id=game_id,
        players=players,
        player_claims=player_claims,
        pending_requests=pending_requests,
    )


@app.route("/admin/<game_id>/ban", methods=["POST"])
def admin_ban(game_id):
    if not is_admin(game_id):
        abort(403)

    player_name = request.form.get("player_name", "").strip()
    if not player_name:
        abort(400)

    db = get_db()
    cur = get_cursor()
    cur.execute("SELECT name FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    cur.execute(
        "UPDATE players SET is_banned = 1 WHERE game_id = %s AND player_name = %s",
        (game_id, player_name),
    )
    cur.execute(
        "DELETE FROM claims WHERE game_id = %s AND player_name = %s",
        (game_id, player_name),
    )
    count = get_claim_count(game_id)
    if count < 100:
        cur.execute("UPDATE games SET is_complete = 0 WHERE id = %s", (game_id,))
    db.commit()

    if game:
        send_discord_notification(SUPERADMIN_DISCORD_WEBHOOK, f"Player '{player_name}' BANNED from '{game['name']}' (ID: {game_id})")

    flash(f"Banned '{player_name}' and removed their claims.", "success")
    return redirect(url_for("admin_players", game_id=game_id))


@app.route("/admin/<game_id>/unban", methods=["POST"])
def admin_unban(game_id):
    if not is_admin(game_id):
        abort(403)

    player_name = request.form.get("player_name", "").strip()
    if not player_name:
        abort(400)

    db = get_db()
    cur = get_cursor()
    cur.execute(
        "UPDATE players SET is_banned = 0 WHERE game_id = %s AND player_name = %s",
        (game_id, player_name),
    )
    db.commit()

    flash(f"Unbanned '{player_name}'.", "success")
    return redirect(url_for("admin_players", game_id=game_id))


@app.route("/admin/<game_id>/approve-request", methods=["POST"])
def admin_approve_request(game_id):
    if not is_admin(game_id):
        abort(403)

    player_name = request.form.get("player_name", "").strip()
    if not player_name:
        abort(400)

    db = get_db()
    cur = get_cursor()
    cur.execute("SELECT max_claims FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game:
        abort(404)

    cur.execute(
        "UPDATE square_requests SET status = 'approved' WHERE game_id = %s AND player_name = %s AND status = 'pending'",
        (game_id, player_name),
    )
    bonus = game["max_claims"] if game["max_claims"] > 0 else 5
    cur.execute(
        "UPDATE players SET bonus_claims = bonus_claims + %s WHERE game_id = %s AND player_name = %s",
        (bonus, game_id, player_name),
    )
    db.commit()

    flash(f"Approved {bonus} extra squares for '{player_name}'.", "success")
    return redirect(url_for("admin_players", game_id=game_id))


@app.route("/admin/<game_id>/deny-request", methods=["POST"])
def admin_deny_request(game_id):
    if not is_admin(game_id):
        abort(403)

    player_name = request.form.get("player_name", "").strip()
    if not player_name:
        abort(400)

    db = get_db()
    cur = get_cursor()
    cur.execute(
        "UPDATE square_requests SET status = 'denied' WHERE game_id = %s AND player_name = %s AND status = 'pending'",
        (game_id, player_name),
    )
    db.commit()

    flash(f"Denied request from '{player_name}'.", "success")
    return redirect(url_for("admin_players", game_id=game_id))


@app.route("/admin/<game_id>/release", methods=["POST"])
def admin_release(game_id):
    if not is_admin(game_id):
        abort(403)

    generate_and_store_numbers(game_id)
    db = get_db()
    cur = get_cursor()
    cur.execute("SELECT name FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    cur.execute(
        "UPDATE games SET numbers_released = 1 WHERE id = %s", (game_id,)
    )
    db.commit()

    if game:
        send_discord_notification(SUPERADMIN_DISCORD_WEBHOOK, f"Numbers RELEASED for '{game['name']}' (ID: {game_id})")

    flash("Numbers have been released to players!", "success")
    return redirect(url_for("admin_panel", game_id=game_id))


@app.route("/admin/<game_id>/lock", methods=["POST"])
def admin_lock(game_id):
    if not is_admin(game_id):
        abort(403)

    db = get_db()
    cur = get_cursor()
    cur.execute("SELECT is_locked, name FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game:
        abort(404)

    if game["is_locked"]:
        # Unlock: remove VOID claims and reopen
        cur.execute(
            "DELETE FROM claims WHERE game_id = %s AND player_name = 'VOID'",
            (game_id,),
        )
        cur.execute("UPDATE games SET is_locked = 0 WHERE id = %s", (game_id,))
        count = get_claim_count(game_id)
        if count < 100:
            cur.execute("UPDATE games SET is_complete = 0 WHERE id = %s", (game_id,))
        db.commit()
        send_discord_notification(SUPERADMIN_DISCORD_WEBHOOK, f"Game '{game['name']}' UNLOCKED (ID: {game_id})")
        flash("Game unlocked. Players can join and claim spots again.", "success")
    else:
        # Lock: fill unclaimed spots with VOID
        now = datetime.datetime.now().isoformat()
        for r in range(1, 11):
            for c in range(1, 11):
                cur.execute(
                    'INSERT INTO claims (game_id, "row", col, player_name, claimed_at) '
                    'VALUES (%s, %s, %s, %s, %s) ON CONFLICT (game_id, "row", col) DO NOTHING',
                    (game_id, r, c, 'VOID', now),
                )
        cur.execute("UPDATE games SET is_locked = 1, is_complete = 1 WHERE id = %s", (game_id,))
        db.commit()
        send_discord_notification(SUPERADMIN_DISCORD_WEBHOOK, f"Game '{game['name']}' LOCKED (ID: {game_id})")
        flash("Game locked. Unclaimed spots marked as VOID.", "success")

    return redirect(url_for("admin_panel", game_id=game_id))


@app.route("/admin/<game_id>/pdf")
def admin_pdf(game_id):
    if not is_admin(game_id):
        abort(403)

    grid, game = build_grid_from_db(game_id)
    if not grid:
        abort(404)

    col_numbers = json.loads(game["col_numbers"])
    if not col_numbers:
        flash("Numbers have not been generated yet.", "error")
        return redirect(url_for("admin_panel", game_id=game_id))

    pdf_path = export_grid_to_pdf(grid)
    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=f"grid_{game_id}.pdf",
    )


@app.route("/admin/<game_id>/remove", methods=["POST"])
def admin_remove(game_id):
    if not is_admin(game_id):
        abort(403)

    row = request.form.get("row", type=int)
    col = request.form.get("col", type=int)
    if row is None or col is None:
        abort(400)

    db = get_db()
    cur = get_cursor()
    cur.execute(
        'DELETE FROM claims WHERE game_id = %s AND "row" = %s AND col = %s',
        (game_id, row, col),
    )
    count = get_claim_count(game_id)
    if count < 100:
        cur.execute("UPDATE games SET is_complete = 0 WHERE id = %s", (game_id,))
    db.commit()
    flash(f"Removed claim at row {row}, col {col}.", "success")
    return redirect(url_for("admin_panel", game_id=game_id))


@app.route("/admin/<game_id>/reply", methods=["POST"])
@limiter.limit("10 per minute")
def admin_reply(game_id):
    if not is_admin(game_id):
        abort(403)

    player_name = request.form.get("player_name", "").strip()
    message = request.form.get("message", "").strip()
    if not player_name or not message:
        flash("Reply cannot be empty.", "error")
        return redirect(url_for("admin_panel", game_id=game_id))
    if len(message) > 500:
        flash("Reply must be 500 characters or less.", "error")
        return redirect(url_for("admin_panel", game_id=game_id))

    db = get_db()
    cur = get_cursor()
    now = datetime.datetime.now().isoformat()
    cur.execute(
        "INSERT INTO messages (game_id, player_name, message, sent_at, sender_type) VALUES (%s, %s, %s, %s, 'host')",
        (game_id, player_name, message, now),
    )
    db.commit()

    cur.execute("SELECT name, discord_webhook FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if game:
        send_discord_notification(
            game["discord_webhook"],
            f"Host replied to '{player_name}' in '{game['name']}':\n> {message}",
        )

    flash(f"Reply sent to {player_name}.", "success")
    return redirect(url_for("admin_panel", game_id=game_id))


# ── Routes: Super Admin ───────────────────────────────────────────

@app.route("/superadmin", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def superadmin_login():
    if is_superadmin():
        return redirect(url_for("superadmin_dashboard"))

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        if password == SUPER_ADMIN_PASSWORD:
            session["is_superadmin"] = True
            return redirect(url_for("superadmin_dashboard"))
        else:
            flash("Wrong password.", "error")

    return render_template("superadmin_login.html")


@app.route("/superadmin/dashboard")
def superadmin_dashboard():
    if not is_superadmin():
        return redirect(url_for("superadmin_login"))

    cur = get_cursor()
    cur.execute("SELECT * FROM games ORDER BY created_at DESC")
    all_games = cur.fetchall()

    games = []
    for game in all_games:
        games.append({
            "id": game["id"],
            "name": game["name"],
            "team_x": game["team_x"],
            "team_y": game["team_y"],
            "created_at": game["created_at"],
            "claim_count": get_claim_count(game["id"]),
            "player_count": get_player_count(game["id"]),
            "locked": is_game_locked(game),
            "is_locked": game["is_locked"],
            "numbers_released": game["numbers_released"],
        })

    return render_template("superadmin_dashboard.html", games=games)


@app.route("/superadmin/shutdown/<game_id>", methods=["POST"])
def superadmin_shutdown(game_id):
    if not is_superadmin():
        abort(403)

    db = get_db()
    cur = get_cursor()
    cur.execute("SELECT name FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game:
        abort(404)

    game_name = game["name"]
    cur.execute("DELETE FROM messages WHERE game_id = %s", (game_id,))
    cur.execute("DELETE FROM square_requests WHERE game_id = %s", (game_id,))
    cur.execute("DELETE FROM claims WHERE game_id = %s", (game_id,))
    cur.execute("DELETE FROM players WHERE game_id = %s", (game_id,))
    cur.execute("DELETE FROM games WHERE id = %s", (game_id,))
    db.commit()

    send_discord_notification(SUPERADMIN_DISCORD_WEBHOOK, f"Game '{game_name}' DELETED (ID: {game_id})")

    flash(f"Game '{game_name}' has been shut down and deleted.", "success")
    return redirect(url_for("superadmin_dashboard"))


@app.route("/superadmin/lock/<game_id>", methods=["POST"])
def superadmin_lock(game_id):
    if not is_superadmin():
        abort(403)

    db = get_db()
    cur = get_cursor()
    cur.execute("SELECT is_locked, name FROM games WHERE id = %s", (game_id,))
    game = cur.fetchone()
    if not game:
        abort(404)

    if game["is_locked"]:
        cur.execute(
            "DELETE FROM claims WHERE game_id = %s AND player_name = 'VOID'",
            (game_id,),
        )
        cur.execute("UPDATE games SET is_locked = 0 WHERE id = %s", (game_id,))
        count = get_claim_count(game_id)
        if count < 100:
            cur.execute("UPDATE games SET is_complete = 0 WHERE id = %s", (game_id,))
        db.commit()
        send_discord_notification(SUPERADMIN_DISCORD_WEBHOOK, f"Game '{game['name']}' UNLOCKED by Super Admin (ID: {game_id})")
        flash("Game unlocked.", "success")
    else:
        now = datetime.datetime.now().isoformat()
        for r in range(1, 11):
            for c in range(1, 11):
                cur.execute(
                    'INSERT INTO claims (game_id, "row", col, player_name, claimed_at) '
                    'VALUES (%s, %s, %s, %s, %s) ON CONFLICT (game_id, "row", col) DO NOTHING',
                    (game_id, r, c, 'VOID', now),
                )
        cur.execute("UPDATE games SET is_locked = 1, is_complete = 1 WHERE id = %s", (game_id,))
        db.commit()
        send_discord_notification(SUPERADMIN_DISCORD_WEBHOOK, f"Game '{game['name']}' LOCKED by Super Admin (ID: {game_id})")
        flash("Game locked.", "success")

    return redirect(url_for("superadmin_dashboard"))


@app.route("/superadmin/logout")
def superadmin_logout():
    session.pop("is_superadmin", None)
    flash("Logged out of super admin.", "success")
    return redirect(url_for("index"))


init_db()

if __name__ == "__main__":
    print("Starting Number Football Grid server...")
    print("Open http://localhost:5000 in your browser")
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", host="0.0.0.0", port=5000)
