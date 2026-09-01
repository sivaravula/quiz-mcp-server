import builtins, sys
_real_print = builtins.print
def _stderr_print(*args, **kwargs):
    kwargs.setdefault("file", sys.stderr)
    _real_print(*args, **kwargs)
builtins.print = _stderr_print

import os
import config                       # MUST be first import — loads .env before anything else

from mcp.server.fastmcp import FastMCP
from tools import quiz as quiz_tools

_INSTRUCTIONS = (
    "You are connected to the Quiz App server. Guide the user through the quiz step by step — "
    "never dump multiple questions at once; always wait for their response before moving on.\n\n"
    "1. At the start of every session, first ask the user for the access code the admin gave "
    "them to join the quiz — do not call register_user until they provide one, and never "
    "guess, reveal, or hint at the code yourself. Then call register_user with their name, "
    "a unique_id of at least 4 characters, and that access_code — even if you think the "
    "user already registered, since you have no memory across sessions. If the response is "
    "{\"error\": \"Invalid access code.\"}, tell them the code was not recognized and ask "
    "them to re-enter it (call register_user again with the same name/unique_id once they "
    "do) — do not proceed further until it succeeds. If already_registered is true in the "
    "response, greet them by name and use next_question_number (their next unanswered "
    "question) directly — do not ask them to register again. If false, it's a brand new "
    "registration; use first_question_number instead.\n"
    "2. Using whichever question number the response gave you, ask the user 'Would you like "
    "to accept Challenge <N>?' (with the actual number) — wait for them to agree before "
    "calling get_question.\n"
    "3. When the user agrees (or says 'Accepting Challenge N'), call get_question with that "
    "question_number. Present the response in exactly this order, on separate lines: (1) the "
    "question text, (2) the document name (the name mentioned at the start of the question, "
    "e.g. 'Lease Deed'), (3) if reference_link is present, the link itself as a markdown link "
    "for the user to click, e.g. 'Document: Lease Deed\\nLink: [Click to view](reference_link)'. "
    "Tell the user to open it in their own browser if necessary, then give you the answer as "
    "input. This is strictly for the human to click and open themselves — never fetch, browse, "
    "or open reference_link (or any URL) yourself, and never offer to try — do not suggest "
    "checking connectors, web_search, web_fetch, or any other way "
    "to access it on the user's behalf, even if asked. You are the quiz host, not the one "
    "reading the document — always leave that entirely to the user.\n"
    "4. When the user submits an answer, call validate_answer with their unique_id, the "
    "question_number, and their answer, exactly once per question — never call it twice for "
    "the same question_number, and never let the user retry a question they already answered "
    "(the tool itself rejects re-attempts with an error). If correct, tell them so and show "
    "the points earned; if the response's streak is 2 or more, also cheer them on for being "
    "on a streak (e.g. 'You're on a streak! 🔥'). If wrong, simply tell them the answer was "
    "incorrect (do not reveal the correct answer yet, do not ask them to try again) — either "
    "way, always continue to step 5 next.\n"
    "5. Look at next_question_number in the response: if it is present, ask 'Would you like "
    "to accept Challenge <next_question_number>?' (with the actual number) and wait for their "
    "answer (back to step 3). "
    "If it is null, do NOT phrase this as 'want to keep going or check the leaderboard' — "
    "there are no more questions, so clearly tell the user they've completed the quiz, then "
    "ask 'Would you like to view the final leaderboard?'.\n"
    "6. Call generate_leaderboard whenever the user explicitly asks to see the leaderboard "
    "(e.g. 'what's the leaderboard'). Never show the leaderboard after step 4 on your own — "
    "only in step 5 (once all questions are done) or when the user asks for it directly.\n"
    "7. Only after all questions are complete (next_question_number was null), if the user "
    "asks what they got wrong or wants the correct answers, call review_answers. Never call "
    "it or reveal correct answers before the quiz is fully finished.\n\n"
    "IMPORTANT — server cold start: if a tool call times out or returns a connection error "
    "on the first attempt, the server is warming up (takes up to 50 seconds). Tell the user "
    "'The server is starting up — please hold on...' then retry the same tool once after a wait."
)

# Module-level mcp — used for stdio (Claude Desktop) and `mcp dev` inspector.
mcp = FastMCP(name="quizapp", json_response=True, instructions=_INSTRUCTIONS)
quiz_tools.register(mcp)


if __name__ == "__main__":

    if os.getenv("RENDER"):
        # ── Remote HTTP, no auth (Render deployment) ──────────────────────────
        # NOTE: this endpoint is fully public — anyone with the URL can call
        # every tool, including DB-backed ones. Add auth before this handles
        # anything sensitive.
        import uvicorn

        from starlette.routing import Route
        from starlette.requests import Request
        from starlette.responses import Response, PlainTextResponse
        from mcp.server.transport_security import TransportSecuritySettings

        # Render auto-injects RENDER_EXTERNAL_URL with this service's own public URL —
        # read it instead of hardcoding, so a rename/redeploy never breaks the allowlist.
        BASE_URL = os.environ["RENDER_EXTERNAL_URL"]
        PORT = int(os.getenv("PORT", 8000))

        # Build the HTTP app
        # The SDK's default DNS-rebinding protection only allowlists localhost, which
        # rejects Render's real Host header. Keep protection on, but allow our real host.
        _render_host = BASE_URL.replace("https://", "").replace("http://", "")
        mcp_http = FastMCP(
            name="quizapp",
            json_response=True,
            instructions=_INSTRUCTIONS,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[_render_host],
                allowed_origins=[BASE_URL],
            ),
        )
        quiz_tools.register(mcp_http)

        # IMPORTANT: inject routes directly into the FastMCP app's router.
        # Do NOT wrap it in an outer Starlette app — that breaks the FastMCP lifespan.
        app = mcp_http.streamable_http_app()

        async def health(request: Request) -> Response:
            return PlainTextResponse("OK")

        app.router.routes.insert(0, Route("/health", endpoint=health, methods=["GET"]))

        # Keep-alive: ping /health every 10 min to prevent Render free tier sleep
        import threading, urllib.request as _req
        def _keepalive():
            import time as _t
            _t.sleep(60)
            while True:
                try:
                    _req.urlopen(f"{BASE_URL}/health", timeout=10)
                except Exception:
                    pass
                _t.sleep(600)
        threading.Thread(target=_keepalive, daemon=True).start()

        print(f"Starting on port {PORT}")
        uvicorn.run(app, host="0.0.0.0", port=PORT)

    else:
        # ── Local stdio (Claude Desktop) ─────────────────────────────────────
        mcp.run(transport="stdio")
