"""Conversation memory survives a restart: a new checkpointer connection sees what the old one saved."""
import os

from langchain_core.messages import HumanMessage

from app import agent, memory

from .fakes import ScriptedModel, say


def test_memory_persists_across_connections(seeded, tmp_path):
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(tmp_path / 'mem.db').as_posix()}"
    phone = "923000000199"
    model = ScriptedModel(script=[say("Walaikum assalam!")])
    first = agent.build_graph(model, memory.get_checkpointer(url))
    agent.respond(phone, "AoA, my name is Hira", graph=first)
    second = agent.build_graph(ScriptedModel(script=[say("ok")]), memory.get_checkpointer(url))     # "after a restart"
    state = second.get_state({"configurable": {"thread_id": phone}})
    texts = [m.content for m in state.values["messages"]]
    assert "AoA, my name is Hira" in texts and "Walaikum assalam!" in texts
    other = second.get_state({"configurable": {"thread_id": "923000000198"}})
    assert not other.values                                                                           # another phone: empty memory
