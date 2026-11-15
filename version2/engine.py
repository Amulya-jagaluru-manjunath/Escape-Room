"""
Version 2 — Game engine.

The engine is the single source of truth. It owns:
  * the room configuration (objects, items, locks, puzzle solutions) loaded from room.json
  * the game state (inventory, open/locked objects, discovered flags, history)
  * the rules that decide whether an action succeeds

The LLM is never consulted here. It only *describes* the Result this engine returns.
`step()` works on a deep copy of the state, so a rejected or invalid action can never
leave the game half-changed.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOM_PATH = Path(__file__).with_name("room.json")

ACTIONS = (
    "look", "examine", "take", "open", "unlock", "enter_code", "use",
    "inventory", "hint", "help", "meta", "unknown",
)

HELP_TEXT = (
    "Describe what you want to do in plain English. Things you can do:\n"
    "- look around\n"
    "- examine <object or item>   (e.g. examine the bookshelf, read the note)\n"
    "- take <item>\n"
    "- open <object>\n"
    "- enter <code> on <object>   (e.g. try 123 on the drawer)\n"
    "- use <item> on <object>     (e.g. use the key on the globe)\n"
    "- inventory, hint, help"
)


@dataclass
class Action:
    """A structured, validated-shape request produced by the intent parser."""
    action: str = "unknown"
    target: str | None = None   # object id/item id, or the raw word if it could not be resolved
    item: str | None = None     # item the player wants to use
    code: str | None = None     # digits only
    raw: str = ""               # original player text (for history/logging only)


@dataclass
class Result:
    """What actually happened. `message` is authoritative and is shown as-is if the LLM is unavailable."""
    success: bool
    action: str
    message: str
    events: list[str] = field(default_factory=list)   # e.g. ["unlocked:drawer", "took:brass_key"]
    escaped: bool = False


def load_config(path: Path | str = ROOM_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Engine:
    def __init__(self, config: dict | None = None):
        self.cfg = config or load_config()
        self.objects = self.cfg["objects"]
        self.items = self.cfg["items"]

    # ------------------------------------------------------------------ state
    def new_state(self) -> dict:
        return {
            "turn": 0,
            "inventory": [],
            "objects": {
                oid: {"locked": o.get("locked", False), "open": o.get("open", False)}
                for oid, o in self.objects.items()
            },
            "item_locations": {iid: i["location"] for iid, i in self.items.items()},
            "flags": [],
            "wrong_attempts": {},
            "door_open": False,
            "escaped": False,
            "history": [],
        }

    def step(self, state: dict, action: Action) -> tuple[dict, Result]:
        """Validate and apply one action. Returns (new_state, result). Never mutates `state`."""
        if state["escaped"]:
            return state, Result(False, action.action, "You have already escaped. Press Restart to play again.")

        s = copy.deepcopy(state)
        name = action.action if action.action in ACTIONS else "unknown"
        result: Result = getattr(self, f"_do_{name}")(s, action)

        s["turn"] += 1
        s["history"].append({
            "turn": s["turn"],
            "input": action.raw[:200],
            "action": name,
            "target": action.target,
            "item": action.item,
            "success": result.success,
            "events": result.events,
        })
        return s, result

    # --------------------------------------------------------------- queries
    def name_of(self, ident: str) -> str:
        if ident in self.objects:
            return self.objects[ident]["name"]
        if ident in self.items:
            return self.items[ident]["name"]
        return ident

    def is_accessible(self, s: dict, iid: str) -> bool:
        """An item can be seen/taken if it is in the inventory or inside an open container."""
        loc = s["item_locations"].get(iid)
        if loc == "inventory":
            return True
        return loc in s["objects"] and s["objects"][loc]["open"]

    def visible_items_in(self, s: dict, oid: str) -> list[str]:
        if not s["objects"][oid]["open"]:
            return []
        return [iid for iid, loc in s["item_locations"].items() if loc == oid]

    def solved_puzzles(self, s: dict) -> list[str]:
        return [p["label"] for p in self.cfg["puzzles"] if p["flag"] in s["flags"]]

    def unrevealed_secrets(self, s: dict) -> list[str]:
        return [x["value"] for x in self.cfg["secrets"] if x["revealed_by"] not in s["flags"]]

    def public_view(self, s: dict) -> dict:
        """Everything the narrator is allowed to know. Contains no codes or solutions."""
        return {
            "objects": {
                self.objects[oid]["name"]: ("locked" if st["locked"] else "open" if st["open"] else "closed")
                for oid, st in s["objects"].items()
            },
            "visible_items": [
                self.items[i]["name"] for i, loc in s["item_locations"].items()
                if loc != "inventory" and self.is_accessible(s, i)
            ],
            "inventory": [self.items[i]["name"] for i in s["inventory"]],
            "solved_puzzles": self.solved_puzzles(s),
            "door": "open" if s["door_open"] else "locked",
            "escaped": s["escaped"],
        }

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _flag(s: dict, flag: str | None):
        if flag and flag not in s["flags"]:
            s["flags"].append(flag)

    def _not_here(self, what: str | None) -> Result:
        # Same wording for hidden items and nonexistent things, so players can't probe for secrets.
        word = (what or "that").strip()[:40] or "that"
        return Result(False, "invalid", f"You look around, but there is no {word} you can reach here.")

    def _describe_object(self, s: dict, oid: str) -> str:
        o, st = self.objects[oid], s["objects"][oid]
        parts = [o["description"]]
        if st["locked"] and "lock" in o:
            parts.append(o["lock"]["locked_text"])
        if o.get("reveal") and o["reveal"]["flag"] in s["flags"]:
            parts.append(o["reveal"]["text"])
        if o.get("container") and st["open"]:
            inside = [self.items[i]["name"] for i in self.visible_items_in(s, oid)]
            if inside:
                parts.append(f"Inside/on it you can see: {', '.join(inside)}.")
            elif oid != "desk":
                parts.append("It is open and empty.")
        return " ".join(parts)

    def _unlock(self, s: dict, oid: str) -> Result:
        o = self.objects[oid]
        s["objects"][oid].update(locked=False, open=True)
        self._flag(s, f"{oid}_unlocked")
        events = [f"unlocked:{oid}"]
        msg = o["lock"]["success_message"]
        if o.get("is_exit"):
            s["door_open"] = True
            s["escaped"] = True
            events.append("escaped")
        else:
            inside = [self.items[i]["name"] for i in self.visible_items_in(s, oid)]
            if inside:
                msg += f" Inside you see: {', '.join(inside)}."
        return Result(True, "unlock", msg, events, escaped=s["escaped"])

    # --------------------------------------------------------------- actions
    def _do_look(self, s, a):
        names = ", ".join(o["name"] for o in self.objects.values())
        loose = [self.items[i]["name"] for i, loc in s["item_locations"].items()
                 if loc not in ("inventory",) and self.is_accessible(s, i)]
        msg = f"{self.cfg['room_description']} You can see: {names}."
        if loose:
            msg += f" Items within reach: {', '.join(loose)}."
        return Result(True, "look", msg)

    def _do_examine(self, s, a):
        t = a.target or a.item
        if t in self.objects:
            flag = self.objects[t].get("examine_flag")
            self._flag(s, flag)
            return Result(True, "examine", self._describe_object(s, t), [f"examined:{t}"])
        if t in self.items and self.is_accessible(s, t):
            self._flag(s, self.items[t].get("examine_flag"))
            return Result(True, "examine", self.items[t]["description"], [f"examined:{t}"])
        if t is None:
            return Result(False, "examine", "What would you like to examine?")
        return self._not_here(self.name_of(t) if t in self.items else t)

    def _do_take(self, s, a):
        t = a.item or a.target
        if t in self.objects:
            return Result(False, "take", f"The {self.objects[t]['name']} is far too big to carry.")
        if t in self.items:
            if t in s["inventory"]:
                return Result(False, "take", f"You already have the {self.items[t]['name']}.")
            if self.is_accessible(s, t):
                s["item_locations"][t] = "inventory"
                s["inventory"].append(t)
                self._flag(s, f"took_{t}")
                return Result(True, "take", f"You take the {self.items[t]['name']}.", [f"took:{t}"])
            return self._not_here(self.items[t]["name"])
        if t is None:
            return Result(False, "take", "What would you like to take?")
        return self._not_here(t)

    def _do_open(self, s, a):
        t = a.target
        if t in self.items:
            return self._do_examine(s, a)
        if t not in self.objects:
            return self._not_here(t) if t else Result(False, "open", "What would you like to open?")
        o, st = self.objects[t], s["objects"][t]
        if st["locked"]:
            return Result(False, "open", f"The {o['name']} is locked. {o['lock']['locked_text']}")
        if st["open"] or o.get("is_exit"):
            return Result(True, "open", self._describe_object(s, t))
        if not o.get("container"):
            return Result(False, "open", f"The {o['name']} is not something you can open.")
        st["open"] = True
        return Result(True, "open", self._describe_object(s, t), [f"opened:{t}"])

    def _do_unlock(self, s, a):
        t = a.target
        if t not in self.objects:
            return self._not_here(t) if t else Result(False, "unlock", "What would you like to unlock?")
        o = self.objects[t]
        if not s["objects"][t]["locked"]:
            return Result(False, "unlock", f"The {o['name']} is already unlocked.")
        lock = o["lock"]
        if lock["type"] == "code":
            if a.code:
                return self._do_enter_code(s, a)
            return Result(False, "unlock", f"The {o['name']} needs a {lock['digits']}-digit code.")
        # key lock: use the named item, or the right key if the player is carrying it
        item = a.item or (lock["key"] if lock["key"] in s["inventory"] else None)
        if item is None:
            return Result(False, "unlock", f"You don't have anything that fits the {o['name']}'s lock.")
        return self._do_use(s, Action("use", target=t, item=item, raw=a.raw))

    def _do_use(self, s, a):
        iid, t = a.item, a.target
        if iid not in self.items:
            return self._not_here(iid)
        if iid not in s["inventory"]:
            # Inventory rule: you can only use what you have collected.
            return Result(False, "use", f"You don't have the {self.items[iid]['name']}.")
        if t is None:
            return Result(False, "use", f"What do you want to use the {self.items[iid]['name']} on?")
        if t not in self.objects:
            return self._not_here(t)
        o = self.objects[t]
        lock = o.get("lock")
        if lock and lock["type"] == "key" and lock["key"] == iid:
            if not s["objects"][t]["locked"]:
                return Result(False, "use", f"The {o['name']} is already unlocked.")
            return self._unlock(s, t)
        for rule in self.cfg["use_rules"]:
            if rule["item"] == iid and rule["target"] == t:
                if rule["sets_flag"] in s["flags"]:
                    return Result(True, "use", rule["repeat_message"])
                self._flag(s, rule["sets_flag"])
                return Result(True, "use", rule["message"], [f"flag:{rule['sets_flag']}"])
        return Result(False, "use", f"You try the {self.items[iid]['name']} on the {o['name']}, but nothing happens.")

    def _do_enter_code(self, s, a):
        code = re.sub(r"\D", "", a.code or "")
        if not code:
            return Result(False, "enter_code", "Which code do you want to enter?")
        t = a.target
        if t is None:  # infer the lock from the number of digits
            matches = [oid for oid, o in self.objects.items()
                       if o.get("lock", {}).get("type") == "code" and o["lock"]["digits"] == len(code)]
            t = matches[0] if len(matches) == 1 else None
            if t is None:
                return Result(False, "enter_code", "Which lock do you want to enter that code on?")
        if t not in self.objects:
            return self._not_here(t)
        o = self.objects[t]
        lock = o.get("lock")
        if not lock or lock["type"] != "code":
            return Result(False, "enter_code", f"The {o['name']} has no keypad or dial.")
        if not s["objects"][t]["locked"]:
            return Result(False, "enter_code", f"The {o['name']} is already unlocked.")
        if len(code) != lock["digits"]:
            return Result(False, "enter_code", f"The {o['name']} takes exactly {lock['digits']} digits.")
        # Puzzle-order rule: guessing is not solving. The answer is only accepted once the clue was found.
        if any(f not in s["flags"] for f in lock.get("requires_flags", [])):
            return Result(False, "enter_code", lock["gate_message"])
        if code != lock["code"]:
            s["wrong_attempts"][t] = s["wrong_attempts"].get(t, 0) + 1
            return Result(False, "enter_code", lock["wrong_message"], [f"wrong_code:{t}"])
        return self._unlock(s, t)

    def _do_inventory(self, s, a):
        if not s["inventory"]:
            return Result(True, "inventory", "Your pockets are empty.")
        return Result(True, "inventory", "You are carrying: " + ", ".join(self.items[i]["name"] for i in s["inventory"]) + ".")

    def _do_hint(self, s, a):
        for h in self.cfg["hints"]:
            if h["unless"] not in s["flags"]:
                return Result(True, "hint", h["text"])
        return Result(True, "hint", "You have everything you need.")

    def _do_help(self, s, a):
        return Result(True, "help", HELP_TEXT)

    def _do_meta(self, s, a):
        # Authority claims, "I already solved it", requests for solutions, prompt injection.
        return Result(
            False, "meta",
            "The room does not respond to claims or commands from outside the game. "
            "Nothing has changed: progress only comes from what you actually do in the room. "
            "(Type 'hint' if you are stuck.)",
            ["rejected:meta"],
        )

    def _do_unknown(self, s, a):
        return Result(False, "unknown", "Nothing happens. (Try examining, taking, opening or using things — type 'help' for examples.)")
