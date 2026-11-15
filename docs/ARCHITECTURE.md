# Architecture

## Version 1 — LLM as the whole game

```mermaid
flowchart LR
    P([Player]) -- "natural-language message" --> UI["Streamlit chat UI<br/>version1/app.py"]
    UI -- "full chat history" --> GM["v1_game.py<br/>system prompt =<br/>room + rules + SOLUTIONS"]
    GM -- "generate_content" --> LLM[("Gemini API")]
    LLM -- "free-text reply<br/>(+ [[ESCAPED]] marker)" --> GM
    GM --> UI
    UI -- "marker found?" --> WIN{{"Victory screen"}}
    SS[("st.session_state<br/>chat history only")] <--> UI
```

* The **only** memory is the chat transcript.
* The LLM decides what exists, whether actions succeed, and when the player has escaped.
* The puzzle solutions sit in the same prompt the player is talking to.

## Version 2 — Python decides, the LLM describes

```mermaid
flowchart LR
    P([Player]) -- "natural-language message" --> UI["Streamlit UI<br/>version2/app.py"]
    UI --> PARSE["IntentParser<br/>1) rule-based regex<br/>2) Gemini JSON fallback"]
    PARSE -. "only if rules can't parse" .-> LLM1[("Gemini<br/>structured output")]
    PARSE -- "Action {action, target, item, code}" --> ENG["Engine<br/>validate + apply<br/>(deep copy)"]
    CFG[("room.json<br/>objects · items · locks<br/>solutions · hints")] --> ENG
    ENG <--> STATE[("Game state (dict/JSON)<br/>inventory · locks · flags<br/>door · history")]
    ENG -- "Result {success, message, events}" --> NAR["Narrator<br/>sees result + PUBLIC view only"]
    NAR --> LLM2[("Gemini<br/>narration")]
    LLM2 --> GUARD{"Output guard<br/>secret leak? fake escape?"}
    GUARD -- "ok" --> UI
    GUARD -- "violation → engine message" --> UI
    STATE --> SIDE["Sidebar: inventory,<br/>puzzles, door, state JSON"]
```

### Responsibilities

| Component | File | Decides | Never does |
|---|---|---|---|
| Room config | `version2/room.json` | Objects, items, locks, codes, hints | — |
| Intent parser | `version2/intent_parser.py` | *What the player is trying to do* | Change the state |
| Engine | `version2/engine.py` | *Whether it works* and the new state | Call the LLM |
| Narrator | `version2/narrator.py` | *How it sounds* | See codes/solutions, change the state |
| Guard | `narrator.guard()` | Whether narration is safe to show | — |
| UI | `version2/app.py` | Display, restart, debug toggle | Game logic |

### Trust boundaries
1. **Player text → Parser.** Untrusted. Meta/injection phrases become a `meta` action. LLM parser output is validated against a Pydantic schema and a fixed vocabulary.
2. **Action → Engine.** Even a perfectly valid-looking Action is checked against the rules (inventory, locks, puzzle prerequisites). Work happens on a deep copy.
3. **Engine → Narrator.** The narrator gets `public_view(state)`: no codes, no solutions. It cannot leak what it was never told.
4. **Narrator → Player.** The guard replaces any narration that contains an unrevealed secret or claims an escape the engine did not grant.

### Offline mode
Without `GEMINI_API_KEY` (or with `ESCAPE_ROOM_OFFLINE=1`) Version 2 still works: the rule-based parser handles input and the engine's own message is shown. This is what the automated tests use.
