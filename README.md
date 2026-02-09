# Number Football Grid

A web app for hosting football squares games. Create a 10x10 grid, invite players to claim squares, and randomly assign numbers to determine winners each quarter.

## How It Works

1. **Host creates a game** -- sets team names, pricing, payment methods, and an optional auto-lock time
2. **Host shares the link or Game Code** with players (custom 6-character code or auto-generated)
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
BROWSE_ACCESS_CODE=your_browse_code_here
SUPERADMIN_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...  (optional)
ENCRYPTION_KEY=your_fernet_key_here  (optional but recommended)
FLASK_DEBUG=1  (optional, local dev only)
```

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Random string for session signing |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SUPER_ADMIN_PASSWORD` | Yes | Password for the `/superadmin` panel |
| `HOST_ACCESS_CODE` | Yes | Code required to create new games (prevents spam) |
| `BROWSE_ACCESS_CODE` | Yes | Code required to browse available games |
| `SUPERADMIN_DISCORD_WEBHOOK` | No | Discord webhook for platform-wide notifications |
| `ENCRYPTION_KEY` | No | Fernet key for encrypting phone numbers (see below) |
| `FLASK_DEBUG` | No | Set to `1` for local development only |

Generate a secret key:

```
python -c "import secrets; print(secrets.token_hex(32))"
```

Generate an encryption key:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
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
- Join games by link or 6-character Game Code
- Claim squares on the grid with live progress (e.g. "You have 3/5 squares claimed")
- View all joined games in "My Games"
- **Create an account** or have super admin create one -- log in from any device and your games persist
- Recover session if you lose access (name + last 4 digits of phone)
- Download PDF of the completed grid
- Request more squares if you hit the per-player limit
- Two-way chat with the game host
- Player names restricted to letters, numbers, and spaces only
- Duplicate name protection via phone number

### For Game Hosts
- Choose a custom 6-character Game Code or auto-generate one
- Set team names, square price, payout info
- Add payment methods (CashApp, Venmo, etc.)
- Set max squares per player
- Lock/unlock the grid manually or by auto-lock time
- Release numbers when ready
- Ban/unban players and remove individual claims
- Approve or deny player requests for extra squares
- **Grant extra squares** directly to any player (auto-notifies them via chat)
- **Broadcast a message** to all players in a game at once
- Two-way chat with players (per-player threads on admin panel)
- **Unread message badges** on admin dashboard and chat threads
- "View as Player" button to preview the player experience
- Download grid as PDF
- Recover host access via Game ID + admin password ("Host Login")
- Discord webhook notifications (player joins, claims, messages, requests, grid full, claim limit reached, lock/unlock)

### For Super Admin
- Platform-level admin panel at `/superadmin`
- View all games across the platform with unread message counts
- **Create and manage user accounts** (username + password)
- Manage any game without needing its password
- Lock/unlock or delete any game
- Discord webhook notifications for game events across the platform

## Security

- **Host access code** -- only people with the code can create games
- **Browse access code** -- only people with the code can browse available games
- **CSRF protection** on all forms via Flask-WTF
- **Brute-force lockout** -- 5 failed attempts triggers a 15-minute timeout on all login and gate routes, with Discord alert to super admin
- **Rate limiting** on logins (5/min), game creation (5/min), joins (10/min), claims (20/min), messages (3/min)
- **Secure cookies** with HttpOnly and SameSite flags
- **HTTPS enforcement** via ProxyFix for reverse proxy deployments
- **Cryptographically secure** number generation using Python's `secrets` module
- **User session cap** -- max 50 game sessions stored per user account to prevent data bloat
- **Deleted user auto-revoke** -- if superadmin deletes an account, active sessions are invalidated on next action
- **SSRF protection** -- Discord webhook URLs validated against known prefixes
- **Debug mode disabled** in production by default
- **Reserved name blocking** ("VOID" cannot be used as a player name)
- **Phone number encryption** -- player phone numbers encrypted at rest using Fernet (AES-128-CBC + HMAC); only last 4 digits stored in plaintext for recovery lookups
- **Input validation** -- player names and usernames validated with strict regex patterns

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
    game_grid.html    -- Player grid view + chat
    host_gate.html    -- Host access code gate
    browse_gate.html  -- Browse access code gate
    user_login.html   -- User account login
    recover.html      -- Player session recovery
    admin_recover.html -- Host login recovery
    admin_create.html -- Create game form
    admin_login.html  -- Game admin login
    admin_dashboard.html -- Admin's games list
    admin_panel.html  -- Admin grid management + chat
    admin_players.html -- Player management
    superadmin_login.html -- Super admin login
    superadmin_dashboard.html -- Platform admin
```
