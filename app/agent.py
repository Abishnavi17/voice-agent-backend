"""agent.py - the reply model behind generate_reply(history). (Unchanged.)"""

from __future__ import annotations

from functools import lru_cache

from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline

_MODEL_ID = "google/flan-t5-small"
_HISTORY_WINDOW = 6


@lru_cache(maxsize=1)
def _get_pipeline():
    tokenizer = AutoTokenizer.from_pretrained(_MODEL_ID)
    model = AutoModelForSeq2SeqLM.from_pretrained(_MODEL_ID)
    return pipeline("text2text-generation", model=model, tokenizer=tokenizer)


def _build_prompt(history: list[dict[str, str]]) -> str:
    recent = history[-_HISTORY_WINDOW:]
    lines = [
        "You are a concise, helpful voice assistant. "
        "Answer the final user message using the conversation for context."
    ]
    for turn in recent:
        user = turn.get("user", "").strip()
        agent = turn.get("agent", "").strip()
        if user:
            lines.append(f"User: {user}")
        if agent:
            lines.append(f"Assistant: {agent}")
    lines.append("Assistant:")
    return "\n".join(lines)


def generate_reply(history: list[dict[str, str]]) -> str:
    prompt = _build_prompt(history)
    generator = _get_pipeline()
    output = generator(prompt, max_new_tokens=128, do_sample=False)
    return output[0]["generated_text"].strip()
