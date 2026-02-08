# Number Football Grid

A web app for hosting football squares games. Create a 10x10 grid, invite players to claim squares, and randomly assign numbers to determine winners each quarter.

## How It Works

1. **Host creates a game** -- sets team names, pricing, payment methods, and an optional auto-lock time
2. **Host shares the link or Game ID** with players
3. **Players join and claim squares** on the 10x10 grid
4. **Host locks the grid** when ready (or it auto-locks at game time) -- unclaimed squares become VOID
5. **Host releases the numbers** -- each row and column gets a random 0-9 digit
6. **Winners are determined** by matching the last digit of each team's score to the grid

## Setup

### Requirements

- Python 3.10+
- pip

### Install Dependencies

```
pip install flask fpdf2 python-dotenv flask-wtf flask-limiter
```

### Configure Environment

Create a `.env` file in the project root:

```
SUPER_ADMIN_PASSWORD=your_secure_password_here
SECRET_KEY=your_random_secret_key_here
```

Generate a secret key:

```
python -c "import secrets; print(secrets.token_hex(32))"
```

If no `.env` file exists, the app uses defaults (`admin1234` for super admin, random key per restart).

### Run the App

```
python app.py
```

Open `http://localhost:5000` in your browser. The app listens on all interfaces (`0.0.0.0`) so other devices on your network can connect using your local IP.

## Features

### For Players
- Join games by link or Game ID
- Claim squares on the grid
- View all joined games in "My Games"
- Download PDF of the completed grid
- Request more squares if you hit the per-player limit

### For Game Hosts
- Set team names, square price, payout info
- Add payment methods (CashApp, Venmo, etc.)
- Set max squares per player
- Lock/unlock the grid manually or by auto-lock time
- Release numbers when ready
- Ban/unban players and remove individual claims
- Approve or deny player requests for extra squares
- Download grid as PDF

### For Super Admin
- Platform-level admin panel at `/superadmin`
- View all games across the platform
- Manage any game without needing its password
- Lock/unlock or delete any game

## Important Notes

- **Database**: SQLite (`game.db`) -- created automatically on first run
- **Sessions**: Player and admin sessions are stored in browser cookies signed with `SECRET_KEY`. Changing the key logs everyone out (games and data are not affected)
- **Debug mode**: Currently enabled (`debug=True` in `app.py`). Set to `False` before deploying publicly
-
  ```
  pip install waitress
  waitress-serve --host=0.0.0.0 --port=5000 app:app
  ```

## Project Structure

```
num_foot/
  app.py              -- Main Flask app (all routes and database)
  game.py             -- Grid class and PDF export
  .env                -- Passwords and secret key (not committed)
  .gitignore          -- Ignores .env, game.db, grids/, etc.
  static/
    style.css         -- Dark theme styles
  templates/
    base.html         -- Layout with header nav
    index.html        -- Home page
    browse_games.html -- Available games list
    my_games.html     -- Player's joined games
    join_game.html    -- Join game form
    game_grid.html    -- Player grid view
    admin_create.html -- Create game form
    admin_login.html  -- Game admin login
    admin_dashboard.html -- Admin's games list
    admin_panel.html  -- Admin grid management
    admin_players.html -- Player management
    superadmin_login.html -- Super admin login
    superadmin_dashboard.html -- Platform admin
```
