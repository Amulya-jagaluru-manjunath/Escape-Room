"""Version 1 — Interactive AI Escape Room (Streamlit UI).

Run from the repository root:  streamlit run version1/app.py
"""
import streamlit as st

import v1_game as game

st.set_page_config(page_title="AI Escape Room · v1", page_icon="📚")

if "history" not in st.session_state:
    st.session_state.history = []      # what is sent to the LLM
    st.session_state.escaped = False


def restart():
    st.session_state.history = []
    st.session_state.escaped = False


with st.sidebar:
    st.header("📚 The Halvorsen Library")
    st.caption("Version 1 — the LLM runs the whole game")
    st.markdown(
        """
**How to play**
Type what you want to do in plain English, for example:
- *look around*
- *examine the bookshelf*
- *take the note*
- *try 123 on the drawer*
- *use the key on the globe*

Find the clues, solve the puzzles, open the door.
"""
    )
    st.button("🔄 Restart game", on_click=restart, use_container_width=True)

st.title("AI Escape Room")
st.caption("Version 1 · Gemini as Game Master")

if not game.has_api_key():
    st.error("No Gemini API key found. Copy `.env.example` to `.env` and set `GEMINI_API_KEY`.")
    st.stop()

with st.chat_message("assistant", avatar="🕯️"):
    st.markdown(game.INTRO)

for msg in st.session_state.history:
    with st.chat_message(msg["role"], avatar="🕯️" if msg["role"] == "assistant" else None):
        st.markdown(game.strip_marker(msg["content"]))

if st.session_state.escaped:
    st.success("🎉 You escaped the library! Press **Restart game** to play again.")
    st.stop()

if prompt := st.chat_input("What do you do?"):
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant", avatar="🕯️"):
        with st.spinner("The Game Master is thinking…"):
            try:
                reply = game.game_master_reply(st.session_state.history)
            except Exception as exc:  # show API problems instead of crashing
                st.error(f"LLM error: {exc}")
                st.session_state.history.pop()
                st.stop()
        st.markdown(game.strip_marker(reply))
    st.session_state.history.append({"role": "assistant", "content": reply})
    if game.has_escaped(reply):
        st.session_state.escaped = True
        st.balloons()
        st.rerun()
