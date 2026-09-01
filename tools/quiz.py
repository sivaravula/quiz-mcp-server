import config                    # MUST be first if using DB
import hmac
import uuid
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from mcp.server.fastmcp import FastMCP


def _next_question_number(after=None):
    with config.engine.connect() as conn:
        if after is None:
            row = conn.execute(text("SELECT MIN(question_number) AS qn FROM questions")).fetchone()
        else:
            row = conn.execute(
                text("SELECT MIN(question_number) AS qn FROM questions WHERE question_number > :after"),
                {"after": after},
            ).fetchone()
    return row.qn


def _next_unanswered_question(user_id):
    with config.engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT MIN(q.question_number) AS qn
                FROM questions q
                WHERE q.question_number NOT IN (
                    SELECT question_number FROM attempts WHERE user_id = :uid
                )
            """),
            {"uid": user_id},
        ).fetchone()
    return row.qn


def _total_questions():
    with config.engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) AS c FROM questions")).fetchone().c


def _current_streak(user_id):
    with config.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT is_correct FROM attempts WHERE user_id = :uid ORDER BY question_number"),
            {"uid": user_id},
        ).fetchall()
    streak = 0
    for r in reversed(rows):
        if not r.is_correct:
            break
        streak += 1
    return streak


def _leaderboard():
    with config.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT u.full_name, COALESCE(SUM(a.points_earned), 0) AS total_points
            FROM users u
            LEFT JOIN attempts a ON a.user_id = u.id
            GROUP BY u.id, u.full_name
            ORDER BY total_points DESC
        """)).fetchall()
    return [
        {"rank": i + 1, "name": r.full_name, "points": r.total_points}
        for i, r in enumerate(rows)
    ]


def register(mcp: FastMCP) -> None:
    """Register all tools in this file onto the given FastMCP instance."""

    @mcp.tool()
    def register_user(name: str, unique_id: str, access_code: str) -> dict:
        """
        Register a new user for the quiz, or resume an existing one. Requires the
        access_code the admin shared with participants — ask the user for it and
        pass it here before anything else; do not reveal what the correct code is.
        If unique_id already belongs to a registered user, this returns their info
        and their next unanswered question instead of erroring — always call this
        at the start of a session and use whatever it returns.

        Args:
            name: The user's full name.
            unique_id: A unique id chosen by the user, at least 4 characters.
            access_code: The quiz access code provided by the admin.
        """
        if not hmac.compare_digest((access_code or "").strip(), config.QUIZ_ACCESS_CODE):
            return {"error": "Invalid access code."}

        unique_id = unique_id.strip()
        if len(unique_id) < 4:
            return {"error": "unique_id must be at least 4 characters."}

        with config.engine.connect() as conn:
            existing = conn.execute(
                text("SELECT id, full_name FROM users WHERE unique_id = :uid"),
                {"uid": unique_id},
            ).fetchone()

        if existing:
            return {
                "message": f"Welcome back, {existing.full_name}!",
                "unique_id": unique_id,
                "already_registered": True,
                "next_question_number": _next_unanswered_question(existing.id),
            }

        try:
            with config.engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO users (id, unique_id, full_name) VALUES (:id, :unique_id, :full_name)"),
                    {"id": str(uuid.uuid4()), "unique_id": unique_id, "full_name": name},
                )
        except IntegrityError:
            return {"error": f"unique_id '{unique_id}' is already taken."}

        return {
            "message": f"{name} registered successfully.",
            "unique_id": unique_id,
            "already_registered": False,
            "first_question_number": _next_question_number(),
        }

    @mcp.tool()
    def get_question(question_number: int) -> dict:
        """
        Get a quiz question by its number. Call this when the user accepts a
        challenge (e.g. "Accepting Challenge 1" -> question_number=1). May include
        a reference_link — if present, show it to the user as a highlighted link
        for them to open themselves; never fetch it yourself.

        Args:
            question_number: The number of the question to retrieve.
        """
        with config.engine.connect() as conn:
            row = conn.execute(
                text("SELECT question, points, reference_link FROM questions WHERE question_number = :qn"),
                {"qn": question_number},
            ).fetchone()

        if row is None:
            return {"error": f"No question found with number {question_number}."}

        return {
            "question_number": question_number,
            "question": row.question,
            "points": row.points,
            "reference_link": row.reference_link,
        }

    @mcp.tool()
    def validate_answer(unique_id: str, question_number: int, answer: str) -> dict:
        """
        Submit an answer for a question and check if it's correct. Each question can
        only be attempted once per user — call this only the first time the user answers
        a given question_number. Returns whether the answer was correct, points earned,
        and the next question number. Does NOT return the leaderboard — call
        generate_leaderboard separately if needed.

        Args:
            unique_id: The user's unique id from registration.
            question_number: The question being answered.
            answer: The submitted answer.
        """
        with config.engine.begin() as conn:
            user = conn.execute(
                text("SELECT id FROM users WHERE unique_id = :uid"),
                {"uid": unique_id},
            ).fetchone()
            if user is None:
                return {"error": f"No user registered with unique_id '{unique_id}'."}

            question = conn.execute(
                text("SELECT answer, points FROM questions WHERE question_number = :qn"),
                {"qn": question_number},
            ).fetchone()
            if question is None:
                return {"error": f"No question found with number {question_number}."}

            already_attempted = conn.execute(
                text(
                    "SELECT 1 FROM attempts WHERE user_id = :user_id AND question_number = :qn"
                ),
                {"user_id": user.id, "qn": question_number},
            ).fetchone()
            if already_attempted:
                return {
                    "error": f"Question {question_number} has already been attempted. Re-attempts are not allowed.",
                    "next_question_number": _next_question_number(after=question_number),
                }

            is_correct = answer.strip().lower() == question.answer.strip().lower()
            points_earned = question.points if is_correct else 0

            conn.execute(
                text(
                    "INSERT INTO attempts (user_id, question_number, submitted_answer, is_correct, points_earned) "
                    "VALUES (:user_id, :qn, :answer, :is_correct, :points_earned)"
                ),
                {"user_id": user.id, "qn": question_number, "answer": answer,
                 "is_correct": is_correct, "points_earned": points_earned},
            )

        return {
            "correct": is_correct,
            "points_earned": points_earned,
            "streak": _current_streak(user.id) if is_correct else 0,
            "next_question_number": _next_question_number(after=question_number),
        }

    @mcp.tool()
    def generate_leaderboard() -> dict:
        """
        Get the current quiz leaderboard, ranked by total points.
        """
        return {"leaderboard": _leaderboard()}

    @mcp.tool()
    def review_answers(unique_id: str) -> dict:
        """
        Show the correct answers for a user's incorrect attempts. Only works once
        the user has answered every question — call this only after next_question_number
        has come back null and the user asks to review what they got wrong.

        Args:
            unique_id: The user's unique id from registration.
        """
        with config.engine.connect() as conn:
            user = conn.execute(
                text("SELECT id FROM users WHERE unique_id = :uid"),
                {"uid": unique_id},
            ).fetchone()
            if user is None:
                return {"error": f"No user registered with unique_id '{unique_id}'."}

            answered = conn.execute(
                text("SELECT COUNT(*) AS c FROM attempts WHERE user_id = :uid"),
                {"uid": user.id},
            ).fetchone().c
            total = _total_questions()
            if answered < total:
                return {"error": "You must answer every question before reviewing answers."}

            rows = conn.execute(
                text("""
                    SELECT a.question_number, a.submitted_answer, a.is_correct, q.answer AS correct_answer
                    FROM attempts a
                    JOIN questions q ON q.question_number = a.question_number
                    WHERE a.user_id = :uid AND a.is_correct = 0
                    ORDER BY a.question_number
                """),
                {"uid": user.id},
            ).fetchall()

        return {
            "incorrect_answers": [
                {
                    "question_number": r.question_number,
                    "your_answer": r.submitted_answer,
                    "correct_answer": r.correct_answer,
                }
                for r in rows
            ]
        }
