# Quiz App MCP server

A Python MCP (Model Context Protocol) server built with FastMCP. Exposes tools to:
- **Claude Desktop**, locally, over stdio
- **claude.ai**, remotely, over HTTP (deployed on Render)

Optionally backed by a MySQL database via SQLAlchemy.

## Project layout

```
.
├── server.py           # entry point — branches on RENDER env var (stdio vs HTTP)
├── config.py            # loads .env, exposes shared SQLAlchemy engine
├── tools/
│   ├── __init__.py
│   └── quiz.py           # tool definitions — register(mcp) adds them to a FastMCP instance
├── .env                  # secrets — never commit
├── .env.example
└── requirements.txt
```

## Setup

```powershell
git clone https://github.com/sathvik1607/Quiz_mcp_server.git
cd Quiz_mcp_server
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
copy .env.example .env
# fill in .env with real DB credentials (or remove the DB_* lines if unused)
```

Sanity-check the DB connection before wiring this into Claude Desktop:

```powershell
.\venv\Scripts\python.exe -c "from config import engine; from sqlalchemy import text; print(engine.connect().execute(text('SELECT 1')).scalar())"
```

This should print `1`. If it hangs or errors, fix `.env`/DB connectivity first — don't try to
debug that through Claude Desktop.

## Run locally (stdio)

```powershell
.\venv\Scripts\python.exe server.py
```

Or use the MCP inspector to call tools manually (browser UI, no Claude Desktop needed — good for
checking each tool's request/response in isolation). Requires Node/npm, since it launches via
`npx`:

```powershell
.\venv\Scripts\mcp.exe dev server.py
```

Use the `mcp.exe` entry point in `venv\Scripts`, not `python -m mcp` — the `mcp` package has no
`__main__` and that form fails.

### Connect to Claude Desktop

Add to `%APPDATA%\Claude\claude_desktop_config.json` (merge into whatever's already there —
don't overwrite the file):

```json
{
  "mcpServers": {
    "quizapp": {
      "command": "C:\\path\\to\\project\\venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\project\\server.py"]
    }
  }
}
```

Use absolute paths matching wherever you cloned the repo.

**Fully quit and restart Claude Desktop** — close it from the system tray, not just the window.
It only reloads MCP config on a full restart.

Then verify it loaded: **Settings → Connectors** → `quizapp` should be listed under "Other
tools" with 6 tools (Register user, Get question, Validate answer, Add question, Generate
leaderboard, Review answers).

### Playing the quiz

Start a **new chat** and be explicit — a vague prompt like "let's start the quiz" can get
misrouted to an unrelated built-in "generate a quiz" flow instead of calling this server's
tools. Instead say something like:

> "Use the register_user tool to register me as \<name> with unique_id \<id>, then start the
> quiz."

From there the server's own instructions (in `server.py`) drive the rest of the flow — one
question at a time, waiting for your answer before moving on.

## Deploy to Render (HTTP, for claude.ai)

1. Push to GitHub (`.env` is gitignored — never commit it).
2. Create a Render **Web Service** connected to the repo.
3. Set environment variables in the Render dashboard:

   | Variable    | Value          |
   |-------------|----------------|
   | `RENDER`    | `true`         |
   | `DB_HOST`   | your DB host   |
   | `DB_USER`   | your DB user   |
   | `DB_PASSWORD` | your DB password |
   | `DB_NAME`   | your DB name   |
   | `QUIZ_ACCESS_CODE` | code shared with participants |
   | `QUIZ_ADMIN_CODE` | code kept by whoever manages quiz content |

4. Start command: `python server.py`
5. In `server.py`, update `BASE_URL` under the `RENDER` branch to your actual Render URL.
6. In **claude.ai → Settings → Connectors**, add: `https://your-app.onrender.com/mcp`

**No auth is currently configured on the HTTP endpoint** — anyone with the URL can call every
tool, including DB-backed ones. This was an explicit choice to keep setup simple for now; revisit
before exposing anything sensitive (see Security below).

### Note

OAuth was removed from the HTTP endpoint for now — we're not using claude.ai against this
server's DB/tools yet, so it wasn't worth the extra complexity. Add it back later if needed.

### Keep-alive

Render's free tier sleeps after 15 minutes idle, which makes the first request after sleep slow
(30–60s) or time out. Mitigations already in place:
- A background thread in `server.py` pings `/health` every 10 minutes.
- Add an external monitor (e.g. UptimeRobot) on `https://your-app.onrender.com/health` every
  5 minutes — **not** on `/mcp`.

## Adding a new tool

Open `tools/quiz.py` and add a function inside `register(mcp)`:

```python
@mcp.tool()
def my_new_tool(param1: str, param2: int) -> dict:
    """
    One-sentence description of what this tool does.
    The agent reads this docstring to decide when to call the tool.

    Args:
        param1: What this string parameter means.
        param2: What this integer parameter means.
    """
    result = do_something(param1, param2)
    return {"result": result}
```

No separate registration step — `register(mcp)` is called for both the stdio and HTTP instances.

## Security

- `register_user` requires an `access_code` argument checked (constant-time) against `QUIZ_ACCESS_CODE` in `.env`. This is the only gate on the public endpoint for participants — share the code out-of-band with them and rotate it by changing `.env` (and redeploying) if it leaks.
- `add_question` requires a separate `admin_code` argument checked (constant-time) against `QUIZ_ADMIN_CODE` in `.env`. Keep this code to yourself — anyone who has it can insert questions into the live quiz. Rotate it the same way as `QUIZ_ACCESS_CODE` if it leaks.
- `.env` is gitignored — never commit it.
- Tools never return raw SQL or expose the DB schema.
- All queries use parameterized SQL (`text("... WHERE id = :id")`, not string interpolation).
- Any user-supplied table/column name should be checked against an allowlist before use in SQL.
- The Render HTTP endpoint has no authentication — treat it as public. Don't add tools that
  expose sensitive data or destructive DB operations until auth is added back.
- Whoever holds the shared `DB_*` credentials is pointed at the same live database — there's no
  separate dev/staging instance. `register_user` and `validate_answer` write directly into it,
  so testing locally adds real rows next to real quiz data. Use a distinctive `unique_id` when
  testing, and don't reuse these credentials past what's needed.
