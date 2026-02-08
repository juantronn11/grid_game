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
- A Supabase account (free tier works) or any PostgreSQL database

### Install Dependencies

```
pip install -r requirements.txt
```

### Set Up the Database

1. Create a free project at [supabase.com](https://supabase.com)
2. Go to **Project Settings > Database > Connection string > URI** (Transaction pooler, port 6543)
3. Copy the connection string

The app automatically creates all tables on first run.

### Configure Environment

Create a `.env` file in the project root:

```
SECRET_KEY=your_random_secret_key_here
DATABASE_URL=postgresql://postgres.XXXXX:YOUR_PASSWORD@aws-0-region.pooler.supabase.com:6543/postgres
SUPER_ADMIN_PASSWORD=your_secure_password_here
HOST_ACCESS_CODE=your_host_code_here
SUPERADMIN_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...  (optional)
FLASK_DEBUG=1  (optional, local dev only)
```

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Random string for session signing |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SUPER_ADMIN_PASSWORD` | Yes | Password for the `/superadmin` panel |
| `HOST_ACCESS_CODE` | Yes | Code required to create new games (prevents spam) |
| `SUPERADMIN_DISCORD_WEBHOOK` | No | Discord webhook for platform-wide notifications |
| `FLASK_DEBUG` | No | Set to `1` for local development only |

Generate a secret key:

```
python -c "import secrets; print(secrets.token_hex(32))"
```

### Run Locally

```
python app.py
```

Open `http://localhost:5000` in your browser.

### Deploy to Railway

1. Push to GitHub
2. Create a new project on [railway.app](https://railway.app) and connect your repo
3. Add **all required environment variables** in Railway's dashboard (Variables tab)
4. Railway auto-detects the `Procfile` and deploys with gunicorn
5. HTTPS is handled automatically by Railway's proxy

## Features

### For Players
- Join games by link or Game ID
- Claim squares on the grid
- View all joined games in "My Games"
- Download PDF of the completed grid
- Request more squares if you hit the per-player limit
- Send messages to the game host
- Duplicate name protection via phone number

### For Game Hosts
- Set team names, square price, payout info
- Add payment methods (CashApp, Venmo, etc.)
- Set max squares per player
- Lock/unlock the grid manually or by auto-lock time
- Release numbers when ready
- Ban/unban players and remove individual claims
- Approve or deny player requests for extra squares
- View player messages on the admin panel
- Download grid as PDF
- Discord webhook notifications (player joins, claims, messages, requests, grid full)

### For Super Admin
- Platform-level admin panel at `/superadmin`
- View all games across the platform
- Manage any game without needing its password
- Lock/unlock or delete any game
- Discord webhook notifications for game events across the platform

## Security

- **Host access code** -- only people with the code can create games
- **CSRF protection** on all forms via Flask-WTF
- **Rate limiting** on logins (5/min), game creation (5/min), joins (10/min), claims (20/min), messages (3/min)
- **Secure cookies** with HttpOnly and SameSite flags
- **HTTPS enforcement** via ProxyFix for reverse proxy deployments
- **Cryptographically secure** number generation using Python's `secrets` module
- **Debug mode disabled** in production by default
- **Reserved name blocking** ("VOID" cannot be used as a player name)

## Project Structure

```
num_foot/
  app.py              -- Main Flask app (routes, database, auth)
  game.py             -- Grid class and PDF export
  requirements.txt    -- Python dependencies
  Procfile            -- Production server config (gunicorn)
  .env                -- Secrets and DB connection (not committed)
  .gitignore          -- Ignores .env, grids/, __pycache__/
  static/
    style.css         -- Dark theme styles
  templates/
    base.html         -- Layout with header nav
    index.html        -- Home page
    browse_games.html -- Available games list
    my_games.html     -- Player's joined games
    join_game.html    -- Join game form
    game_grid.html    -- Player grid view
    host_gate.html    -- Host access code gate
    admin_create.html -- Create game form
    admin_login.html  -- Game admin login
    admin_dashboard.html -- Admin's games list
    admin_panel.html  -- Admin grid management + messages
    admin_players.html -- Player management
    superadmin_login.html -- Super admin login
    superadmin_dashboard.html -- Platform admin
```
