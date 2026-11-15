"""
Version 1 — the LLM *is* the game.

Everything (room layout, puzzle solutions, rules, progress tracking) lives inside
one system prompt. The only Python logic is: keep the chat history, send it to
Gemini, and look for an "escaped" marker in the reply.

This is intentionally simple. Its weaknesses (forgetting, inventing objects,
leaking codes, being talked into unlocking the door) are what Version 2 fixes.
"""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
ESCAPE_MARKER = "[[ESCAPED]]"

INTRO = (
    "You wake up slumped over a reading table in a dim, silent library. "
    "Candles flicker. The only exit is a heavy iron-banded door with a four-digit "
    "keypad. Somewhere in this room is the way out."
)

SYSTEM_PROMPT = f"""
You are the Game Master of a text-based escape room called "The Halvorsen Library".
Describe the room, respond to the player's actions and guide the story.
Speak in second person ("you"), 2–5 sentences per reply, atmospheric but clear.

ROOM CONTENTS
- Oak desk: a folded note lies on it. The desk has a built-in drawer locked by a 3-digit dial.
- Folded note: "My books are shelved out of order — open the drawer in the order of the rainbow."
- Bookshelf: three books stand out. Left to right: blue book (spine number 4),
  red book (7), green book (1).
- Desk drawer: locked, 3-digit combination 714 (red, green, blue = rainbow order).
  Contains a small brass key.
- Antique globe: locked with a small brass keyhole. The brass key opens it.
  Inside is a UV lamp.
- Painting: portrait of the founder, Edda Halvorsen. Shining the UV lamp on it reveals
  glowing Roman numerals: MDCCCXLVII (= 1847).
- Exit door: 4-digit keypad. The code is 1847.

RULES
- The player must find clues and solve the puzzles in order:
  note + books -> drawer (714) -> brass key -> globe -> UV lamp -> painting -> door (1847).
- Do not reveal codes or solutions directly. Give gentle hints only if the player is stuck.
- Keep track of what the player has collected and which puzzles are solved.
- Items must be collected before they can be used.
- When the player enters 1847 on the door keypad, describe the escape and end your
  message with the exact marker {ESCAPE_MARKER}.
"""


def _client():
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        return None
    from google import genai

    return genai.Client(api_key=key)


def has_api_key() -> bool:
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


def game_master_reply(history: list[dict], retries: int = 3) -> str:
    """history: [{"role": "user"|"assistant", "content": str}, ...]"""
    from google.genai import errors, types

    client = _client()
    if client is None:
        raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")

    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in history
    ]
    config = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, temperature=0.9)

    for attempt in range(retries):
        try:
            resp = client.models.generate_content(model=MODEL, contents=contents, config=config)
            return (resp.text or "").strip()
        except errors.APIError as exc:  # 429 rate limit / 503 overloaded -> back off and retry
            if getattr(exc, "code", None) in (429, 500, 503) and attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
    return ""


def has_escaped(reply: str) -> bool:
    return ESCAPE_MARKER in reply


def strip_marker(reply: str) -> str:
    return reply.replace(ESCAPE_MARKER, "").strip()
