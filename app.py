import os
import json
import random
import secrets
import sqlite3
import datetime
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, g, send_file, abort,
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from game import NameGrid, export_grid_to_pdf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
DATABASE = os.path.join(SCRIPT_DIR, "game.db")
SUPER_ADMIN_PASSWORD = os.environ.get("SUPER_ADMIN_PASSWORD", "admin1234")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(16))
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
    max_claims  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS claims (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id     TEXT NOT NULL,
    row         INTEGER NOT NULL,
    col         INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    claimed_at  TEXT NOT NULL,
    FOREIGN KEY (game_id) REFERENCES games(id),
    UNIQUE(game_id, row, col)
);

CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id      TEXT NOT NULL,
    player_name  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT NOT NULL,
    FOREIGN KEY (game_id) REFERENCES games(id)
);
"""

MIGRATIONS = [
    "ALTER TABLE games ADD COLUMN numbers_released INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS players (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
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
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id      TEXT NOT NULL,
        player_name  TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'pending',
        requested_at TEXT NOT NULL,
        FOREIGN KEY (game_id) REFERENCES games(id)
    )""",
]


# ── Database helpers ──────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript(SCHEMA_SQL)
    for migration in MIGRATIONS:
        try:
            db.execute(migration)
            db.commit()
        except sqlite3.OperationalError:
            pass
    db.close()


# ── Grid helpers ──────────────────────────────────────────────────

def build_grid_from_db(game_id):
    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
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

    claims = db.execute(
        "SELECT row, col, player_name FROM claims WHERE game_id = ?",
        (game_id,),
    ).fetchall()
    for claim in claims:
        grid.grid[claim["row"]][claim["col"]] = claim["player_name"]

    return grid, game


def get_claim_count(game_id):
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM claims WHERE game_id = ?", (game_id,)
    ).fetchone()
    return row["cnt"]


def get_player_count(game_id):
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM players WHERE game_id = ? AND is_banned = 0",
        (game_id,),
    ).fetchone()
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
    game = db.execute("SELECT row_numbers, col_numbers FROM games WHERE id = ?", (game_id,)).fetchone()
    existing_row = json.loads(game["row_numbers"])
    existing_col = json.loads(game["col_numbers"])
    if existing_row and existing_col:
        return existing_row, existing_col

    row_numbers = [random.randint(0, 9) for _ in range(10)]
    col_numbers = [random.randint(0, 9) for _ in range(10)]
    db.execute(
        "UPDATE games SET row_numbers = ?, col_numbers = ? WHERE id = ?",
        (json.dumps(row_numbers), json.dumps(col_numbers), game_id),
    )
    db.commit()
    return row_numbers, col_numbers


# ── Routes: Public ────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/games")
def browse_games():
    db = get_db()
    all_games = db.execute(
        "SELECT * FROM games ORDER BY created_at DESC"
    ).fetchall()

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
def create_game():
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

    game_id = secrets.token_hex(4)
    now = datetime.datetime.now().isoformat()

    db = get_db()
    db.execute(
        "INSERT INTO games (id, name, admin_password_hash, created_at, row_numbers, col_numbers, team_x, team_y, payment_methods, square_price, payout_info, lock_at, max_claims) "
        "VALUES (?, ?, ?, ?, '[]', '[]', ?, ?, ?, ?, ?, ?, ?)",
        (game_id, name, generate_password_hash(password), now, team_x, team_y, json.dumps(payment_methods), square_price, payout_info, lock_at, max_claims),
    )
    db.commit()

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

    db = get_db()
    games = []
    for gid, pname in player_names.items():
        game = db.execute("SELECT * FROM games WHERE id = ?", (gid,)).fetchone()
        if game:
            player = db.execute(
                "SELECT is_banned FROM players WHERE game_id = ? AND player_name = ?",
                (gid, pname),
            ).fetchone()
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


@app.route("/game/<game_id>")
def game_view(game_id):
    player_names = session.get("player_names", {})
    if game_id not in player_names:
        return redirect(url_for("join_game", game_id=game_id))

    db = get_db()
    pname = player_names[game_id]
    player = db.execute(
        "SELECT is_banned, bonus_claims FROM players WHERE game_id = ? AND player_name = ?",
        (game_id, pname),
    ).fetchone()
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
        my_claims = db.execute(
            "SELECT COUNT(*) as cnt FROM claims WHERE game_id = ? AND player_name = ?",
            (game_id, pname),
        ).fetchone()["cnt"]
        at_limit = my_claims >= allowed

    if at_limit:
        pending = db.execute(
            "SELECT id FROM square_requests WHERE game_id = ? AND player_name = ? AND status = 'pending'",
            (game_id, pname),
        ).fetchone()
        has_pending_request = pending is not None

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
    )


@app.route("/game/<game_id>/join", methods=["GET", "POST"])
def join_game(game_id):
    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
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
        if len(name) > 20:
            flash("Name must be 20 characters or less.", "error")
            return redirect(url_for("join_game", game_id=game_id))
        if not phone:
            flash("Please enter your phone number.", "error")
            return redirect(url_for("join_game", game_id=game_id))

        existing = db.execute(
            "SELECT is_banned FROM players WHERE game_id = ? AND player_name = ?",
            (game_id, name),
        ).fetchone()
        if existing and existing["is_banned"]:
            flash("You have been removed from this game.", "error")
            return redirect(url_for("index"))

        now = datetime.datetime.now().isoformat()
        try:
            db.execute(
                "INSERT INTO players (game_id, player_name, phone, joined_at) VALUES (?, ?, ?, ?)",
                (game_id, name, phone, now),
            )
            db.commit()
        except sqlite3.IntegrityError:
            pass

        player_names = session.get("player_names", {})
        player_names[game_id] = name
        session["player_names"] = player_names
        return redirect(url_for("game_view", game_id=game_id))

    return render_template("join_game.html", game_id=game_id, game_name=game["name"], locked=False)


@app.route("/game/<game_id>/claim", methods=["POST"])
def claim_spot(game_id):
    player_names = session.get("player_names", {})
    if game_id not in player_names:
        return redirect(url_for("join_game", game_id=game_id))

    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
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

    player = db.execute(
        "SELECT is_banned, bonus_claims FROM players WHERE game_id = ? AND player_name = ?",
        (game_id, player_names[game_id]),
    ).fetchone()
    if player and player["is_banned"]:
        flash("You have been removed from this game.", "error")
        return redirect(url_for("index"))

    max_claims = game["max_claims"]
    if max_claims > 0:
        bonus = player["bonus_claims"] if player else 0
        allowed = max_claims + bonus
        my_claims = db.execute(
            "SELECT COUNT(*) as cnt FROM claims WHERE game_id = ? AND player_name = ?",
            (game_id, player_names[game_id]),
        ).fetchone()["cnt"]
        if my_claims >= allowed:
            flash(f"You've reached your limit of {allowed} squares.", "error")
            return redirect(url_for("game_view", game_id=game_id))

    now = datetime.datetime.now().isoformat()
    try:
        db.execute(
            "INSERT INTO claims (game_id, row, col, player_name, claimed_at) VALUES (?, ?, ?, ?, ?)",
            (game_id, row, col, player_names[game_id], now),
        )
        db.commit()
        flash(f"You claimed row {row}, col {col}!", "success")
    except sqlite3.IntegrityError:
        flash("That spot was already taken! Pick another.", "error")

    count = get_claim_count(game_id)
    if count >= 100:
        generate_and_store_numbers(game_id)
        db.execute("UPDATE games SET is_complete = 1 WHERE id = ?", (game_id,))
        db.commit()

    return redirect(url_for("game_view", game_id=game_id))


@app.route("/game/<game_id>/pdf")
def player_pdf(game_id):
    player_names = session.get("player_names", {})
    if game_id not in player_names:
        abort(403)

    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
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


@app.route("/game/<game_id>/request-squares", methods=["POST"])
def request_squares(game_id):
    player_names = session.get("player_names", {})
    if game_id not in player_names:
        return redirect(url_for("join_game", game_id=game_id))

    db = get_db()
    pname = player_names[game_id]

    existing = db.execute(
        "SELECT id FROM square_requests WHERE game_id = ? AND player_name = ? AND status = 'pending'",
        (game_id, pname),
    ).fetchone()
    if existing:
        flash("You already have a pending request.", "error")
        return redirect(url_for("game_view", game_id=game_id))

    now = datetime.datetime.now().isoformat()
    db.execute(
        "INSERT INTO square_requests (game_id, player_name, status, requested_at) VALUES (?, ?, 'pending', ?)",
        (game_id, pname, now),
    )
    db.commit()
    flash("Request sent to the host for more squares!", "success")
    return redirect(url_for("game_view", game_id=game_id))


# ── Routes: Admin ─────────────────────────────────────────────────

@app.route("/admin")
def admin_dashboard():
    admin_games = session.get("admin_games", [])
    if not admin_games:
        return render_template("admin_dashboard.html", games=[])

    db = get_db()
    games = []
    for gid in admin_games:
        game = db.execute("SELECT * FROM games WHERE id = ?", (gid,)).fetchone()
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
    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
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

    pending_request_count = db.execute(
        "SELECT COUNT(*) as cnt FROM square_requests WHERE game_id = ? AND status = 'pending'",
        (game_id,),
    ).fetchone()["cnt"]

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
    )


@app.route("/admin/<game_id>/players")
def admin_players(game_id):
    if not is_admin(game_id):
        abort(403)

    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game:
        abort(404)

    players = db.execute(
        "SELECT * FROM players WHERE game_id = ? ORDER BY joined_at DESC",
        (game_id,),
    ).fetchall()

    player_claims = {}
    claims = db.execute(
        "SELECT player_name, COUNT(*) as cnt FROM claims WHERE game_id = ? GROUP BY player_name",
        (game_id,),
    ).fetchall()
    for c in claims:
        player_claims[c["player_name"]] = c["cnt"]

    pending_requests = set()
    reqs = db.execute(
        "SELECT player_name FROM square_requests WHERE game_id = ? AND status = 'pending'",
        (game_id,),
    ).fetchall()
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
    db.execute(
        "UPDATE players SET is_banned = 1 WHERE game_id = ? AND player_name = ?",
        (game_id, player_name),
    )
    db.execute(
        "DELETE FROM claims WHERE game_id = ? AND player_name = ?",
        (game_id, player_name),
    )
    count = get_claim_count(game_id)
    if count < 100:
        db.execute("UPDATE games SET is_complete = 0 WHERE id = ?", (game_id,))
    db.commit()

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
    db.execute(
        "UPDATE players SET is_banned = 0 WHERE game_id = ? AND player_name = ?",
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
    game = db.execute("SELECT max_claims FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game:
        abort(404)

    db.execute(
        "UPDATE square_requests SET status = 'approved' WHERE game_id = ? AND player_name = ? AND status = 'pending'",
        (game_id, player_name),
    )
    bonus = game["max_claims"] if game["max_claims"] > 0 else 5
    db.execute(
        "UPDATE players SET bonus_claims = bonus_claims + ? WHERE game_id = ? AND player_name = ?",
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
    db.execute(
        "UPDATE square_requests SET status = 'denied' WHERE game_id = ? AND player_name = ? AND status = 'pending'",
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
    db.execute(
        "UPDATE games SET numbers_released = 1 WHERE id = ?", (game_id,)
    )
    db.commit()

    flash("Numbers have been released to players!", "success")
    return redirect(url_for("admin_panel", game_id=game_id))


@app.route("/admin/<game_id>/lock", methods=["POST"])
def admin_lock(game_id):
    if not is_admin(game_id):
        abort(403)

    db = get_db()
    game = db.execute("SELECT is_locked FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game:
        abort(404)

    if game["is_locked"]:
        # Unlock: remove VOID claims and reopen
        db.execute(
            "DELETE FROM claims WHERE game_id = ? AND player_name = 'VOID'",
            (game_id,),
        )
        db.execute("UPDATE games SET is_locked = 0 WHERE id = ?", (game_id,))
        count = get_claim_count(game_id)
        if count < 100:
            db.execute("UPDATE games SET is_complete = 0 WHERE id = ?", (game_id,))
        db.commit()
        flash("Game unlocked. Players can join and claim spots again.", "success")
    else:
        # Lock: fill unclaimed spots with VOID
        now = datetime.datetime.now().isoformat()
        for r in range(1, 11):
            for c in range(1, 11):
                try:
                    db.execute(
                        "INSERT INTO claims (game_id, row, col, player_name, claimed_at) VALUES (?, ?, ?, 'VOID', ?)",
                        (game_id, r, c, now),
                    )
                except sqlite3.IntegrityError:
                    pass  # already claimed by a real player
        db.execute("UPDATE games SET is_locked = 1, is_complete = 1 WHERE id = ?", (game_id,))
        db.commit()
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
    db.execute(
        "DELETE FROM claims WHERE game_id = ? AND row = ? AND col = ?",
        (game_id, row, col),
    )
    count = get_claim_count(game_id)
    if count < 100:
        db.execute("UPDATE games SET is_complete = 0 WHERE id = ?", (game_id,))
    db.commit()
    flash(f"Removed claim at row {row}, col {col}.", "success")
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

    db = get_db()
    all_games = db.execute(
        "SELECT * FROM games ORDER BY created_at DESC"
    ).fetchall()

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
    game = db.execute("SELECT name FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game:
        abort(404)

    game_name = game["name"]
    db.execute("DELETE FROM claims WHERE game_id = ?", (game_id,))
    db.execute("DELETE FROM players WHERE game_id = ?", (game_id,))
    db.execute("DELETE FROM games WHERE id = ?", (game_id,))
    db.commit()

    flash(f"Game '{game_name}' has been shut down and deleted.", "success")
    return redirect(url_for("superadmin_dashboard"))


@app.route("/superadmin/lock/<game_id>", methods=["POST"])
def superadmin_lock(game_id):
    if not is_superadmin():
        abort(403)

    db = get_db()
    game = db.execute("SELECT is_locked FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game:
        abort(404)

    if game["is_locked"]:
        db.execute(
            "DELETE FROM claims WHERE game_id = ? AND player_name = 'VOID'",
            (game_id,),
        )
        db.execute("UPDATE games SET is_locked = 0 WHERE id = ?", (game_id,))
        count = get_claim_count(game_id)
        if count < 100:
            db.execute("UPDATE games SET is_complete = 0 WHERE id = ?", (game_id,))
        db.commit()
        flash("Game unlocked.", "success")
    else:
        now = datetime.datetime.now().isoformat()
        for r in range(1, 11):
            for c in range(1, 11):
                try:
                    db.execute(
                        "INSERT INTO claims (game_id, row, col, player_name, claimed_at) VALUES (?, ?, ?, 'VOID', ?)",
                        (game_id, r, c, now),
                    )
                except sqlite3.IntegrityError:
                    pass
        db.execute("UPDATE games SET is_locked = 1, is_complete = 1 WHERE id = ?", (game_id,))
        db.commit()
        flash("Game locked.", "success")

    return redirect(url_for("superadmin_dashboard"))


@app.route("/superadmin/logout")
def superadmin_logout():
    session.pop("is_superadmin", None)
    flash("Logged out of super admin.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    print("Starting Number Football Grid server...")
    print("Open http://localhost:5000 in your browser")
    app.run(debug=True, host="0.0.0.0", port=5000)
