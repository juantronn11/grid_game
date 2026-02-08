import os
import json
import random
import secrets
import sqlite3
import datetime
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, g, send_file, abort,
)
from werkzeug.security import generate_password_hash, check_password_hash
from game import NameGrid, export_grid_to_pdf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(SCRIPT_DIR, "game.db")

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS games (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    admin_password_hash TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    row_numbers TEXT NOT NULL,
    col_numbers TEXT NOT NULL,
    is_complete INTEGER NOT NULL DEFAULT 0
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
"""


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


def is_admin(game_id):
    return game_id in session.get("admin_games", [])


# ── Routes ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/create", methods=["GET", "POST"])
def create_game():
    if request.method == "GET":
        return render_template("admin_create.html")

    name = request.form.get("name", "").strip()
    password = request.form.get("password", "").strip()
    confirm = request.form.get("confirm", "").strip()

    if not name:
        flash("Game name is required.", "error")
        return redirect(url_for("create_game"))
    if len(password) < 4:
        flash("Password must be at least 4 characters.", "error")
        return redirect(url_for("create_game"))
    if password != confirm:
        flash("Passwords do not match.", "error")
        return redirect(url_for("create_game"))

    game_id = secrets.token_hex(4)
    row_numbers = []
    col_numbers = []
    now = datetime.datetime.now().isoformat()

    db = get_db()
    db.execute(
        "INSERT INTO games (id, name, admin_password_hash, created_at, row_numbers, col_numbers) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            game_id,
            name,
            generate_password_hash(password),
            now,
            json.dumps(row_numbers),
            json.dumps(col_numbers),
        ),
    )
    db.commit()

    admin_games = session.get("admin_games", [])
    admin_games.append(game_id)
    session["admin_games"] = admin_games

    flash(f"Game '{name}' created!", "success")
    return redirect(url_for("admin_panel", game_id=game_id))


@app.route("/admin/<game_id>", methods=["GET", "POST"])
def admin_panel(game_id):
    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game:
        abort(404)

    # Password gate
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
    col_numbers = json.loads(game["col_numbers"])
    row_numbers = json.loads(game["row_numbers"])

    player_url = request.url_root.rstrip("/") + url_for("game_view", game_id=game_id)

    return render_template(
        "admin_panel.html",
        game=game,
        game_id=game_id,
        grid=grid.grid,
        col_numbers=col_numbers,
        row_numbers=row_numbers,
        claim_count=claim_count,
        player_url=player_url,
    )


@app.route("/admin/<game_id>/pdf")
def admin_pdf(game_id):
    if not is_admin(game_id):
        abort(403)

    grid, game = build_grid_from_db(game_id)
    if not grid:
        abort(404)
    if not game["is_complete"]:
        flash("PDF is only available when the grid is full.", "error")
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
    db.execute("UPDATE games SET is_complete = 0 WHERE id = ?", (game_id,))
    db.commit()
    flash(f"Removed claim at row {row}, col {col}.", "success")
    return redirect(url_for("admin_panel", game_id=game_id))


@app.route("/game/<game_id>")
def game_view(game_id):
    player_names = session.get("player_names", {})
    if game_id not in player_names:
        return redirect(url_for("join_game", game_id=game_id))

    grid, game = build_grid_from_db(game_id)
    if not grid:
        abort(404)

    claim_count = get_claim_count(game_id)
    col_numbers = json.loads(game["col_numbers"])
    row_numbers = json.loads(game["row_numbers"])

    return render_template(
        "game_grid.html",
        game=game,
        game_id=game_id,
        grid=grid.grid,
        col_numbers=col_numbers,
        row_numbers=row_numbers,
        claim_count=claim_count,
        player_name=player_names[game_id],
    )


@app.route("/game/<game_id>/join", methods=["GET", "POST"])
def join_game(game_id):
    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game:
        abort(404)

    if request.method == "POST":
        name = request.form.get("player_name", "").strip()
        if not name:
            flash("Please enter your name.", "error")
            return redirect(url_for("join_game", game_id=game_id))
        if len(name) > 20:
            flash("Name must be 20 characters or less.", "error")
            return redirect(url_for("join_game", game_id=game_id))

        player_names = session.get("player_names", {})
        player_names[game_id] = name
        session["player_names"] = player_names
        return redirect(url_for("game_view", game_id=game_id))

    return render_template("join_game.html", game_id=game_id, game_name=game["name"])


@app.route("/game/<game_id>/claim", methods=["POST"])
def claim_spot(game_id):
    player_names = session.get("player_names", {})
    if game_id not in player_names:
        return redirect(url_for("join_game", game_id=game_id))

    row = request.form.get("row", type=int)
    col = request.form.get("col", type=int)
    if row is None or col is None or not (1 <= row <= 10 and 1 <= col <= 10):
        flash("Invalid spot.", "error")
        return redirect(url_for("game_view", game_id=game_id))

    db = get_db()
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

    # Check if grid is full — generate numbers on completion
    count = get_claim_count(game_id)
    if count >= 100:
        row_numbers = [random.randint(0, 9) for _ in range(10)]
        col_numbers = [random.randint(0, 9) for _ in range(10)]
        db.execute(
            "UPDATE games SET is_complete = 1, row_numbers = ?, col_numbers = ? WHERE id = ?",
            (json.dumps(row_numbers), json.dumps(col_numbers), game_id),
        )
        db.commit()

    return redirect(url_for("game_view", game_id=game_id))


if __name__ == "__main__":
    init_db()
    print("Starting Number Football Grid server...")
    print("Open http://localhost:5000 in your browser")
    app.run(debug=True, host="0.0.0.0", port=5000)
