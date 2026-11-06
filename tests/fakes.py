"""A scripted chat model for tests: returns prepared AI messages in order and records what it was shown."""
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedModel(BaseChatModel):
    script: list = Field(default_factory=list)   # AIMessage, or a callable(messages) -> AIMessage
    seen: list = Field(default_factory=list)     # every message list the model was called with
    idx: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        if self.idx >= len(self.script):
            raise AssertionError("the scripted model was called more often than the test prepared for")
        item = self.script[self.idx]
        self.idx += 1
        return ChatResult(generations=[ChatGeneration(message=item(messages) if callable(item) else item)])


def call(name: str, args: dict, id: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id, "type": "tool_call"}])


def say(text: str) -> AIMessage:
    return AIMessage(content=text)
