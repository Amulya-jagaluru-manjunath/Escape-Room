"""Version 2 — Reliable AI Escape Room (Streamlit UI).

Run from the repository root:  streamlit run version2/app.py

Flow for every message:
  player text -> IntentParser -> Action -> Engine.step (validate + update state) -> Result
             -> Narrator (LLM describes the Result) -> guard -> chat
"""
import json

import streamlit as st

from engine import Engine, load_config
from intent_parser import IntentParser
from llm_client import LLMClient
from narrator import Narrator

st.set_page_config(page_title="AI Escape Room · v2", page_icon="🔐")


def new_game():
    config = load_config()
    llm = LLMClient()
    engine = Engine(config)
    st.session_state.update(
        config=config,
        engine=engine,
        llm=llm,
        parser=IntentParser(config, llm),
        narrator=Narrator(engine, llm),
        state=engine.new_state(),
        messages=[{"role": "assistant", "content": config["intro"], "meta": None}],
    )


if "engine" not in st.session_state:
    new_game()

ss = st.session_state
engine: Engine = ss.engine

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header(f"🔐 {ss.config['title']}")
    st.caption("Version 2 — Python rules engine + LLM narrator")
    st.button("🔄 Restart game", on_click=new_game, use_container_width=True)

    view = engine.public_view(ss.state)
    st.subheader("🎒 Inventory")
    st.write(", ".join(view["inventory"]) or "_empty_")

    st.subheader("🧩 Puzzles")
    for p in ss.config["puzzles"]:
        st.write(("✅ " if p["flag"] in ss.state["flags"] else "⬜ ") + p["label"])

    st.subheader("🚪 Door")
    st.write("Open — you escaped!" if ss.state["door_open"] else "Locked")
    st.caption(f"Turn {ss.state['turn']}")

    show_engine = st.toggle("Show engine decisions", value=False,
                            help="Show the structured action and the engine's verdict under each reply.")
    with st.expander("Game state (JSON)"):
        st.caption("Solutions live in room.json, not in the state.")
        st.code(json.dumps({k: v for k, v in ss.state.items() if k != "history"}, indent=2), language="json")

    with st.expander("How to play"):
        st.markdown(
            "Type what you want to do, e.g. *examine the desk*, *take the note*, "
            "*try 123 on the drawer*, *use the key on the globe*. Type **hint** if stuck."
        )
    mode = "Gemini online" if ss.llm.available else "Offline mode (no API key): rule-based parser, plain narration"
    st.caption(f"LLM: {mode}")

# ---------------------------------------------------------------- chat
st.title("AI Escape Room")
st.caption("Version 2 · Python decides what happens, Gemini describes it")

for m in ss.messages:
    with st.chat_message(m["role"], avatar="🕯️" if m["role"] == "assistant" else None):
        st.markdown(m["content"])
        if show_engine and m.get("meta"):
            st.caption(m["meta"])

if ss.state["escaped"]:
    st.success(f"🎉 You escaped in {ss.state['turn']} turns! Press **Restart game** to play again.")
    st.stop()

if prompt := st.chat_input("What do you do?"):
    action = ss.parser.parse(prompt)
    ss.state, result = engine.step(ss.state, action)
    with st.spinner("The Game Master is thinking…"):
        text, guard_reason = ss.narrator.narrate(prompt, result, ss.state)

    verdict = "✅" if result.success else "❌"
    meta = (f"{verdict} action=`{action.action}` target=`{action.target}` item=`{action.item}` "
            f"code=`{'•' * len(action.code) if action.code else None}` → {result.message}")
    if guard_reason:
        meta += f"  ·  🛡️ guard: {guard_reason}"

    ss.messages.append({"role": "user", "content": prompt, "meta": None})
    ss.messages.append({"role": "assistant", "content": text, "meta": meta})
    if result.escaped:
        st.balloons()
    st.rerun()
