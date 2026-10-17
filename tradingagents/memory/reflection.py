"""Reflection: a settled decision's outcome turned into a short lesson for later runs.

The lesson is requested from the model as a structured JSON object
(``these_claim``, ``vorhersage``, ``outcome_evidenz``, ``lektion``,
``konfidenz``, ``horizonte``) instead of free prose, so later tooling
(trigger tracking, semantic retrieval, calibration) can read it without
parsing prose. The JSON is normalised, rendered into a compact human-readable
text (that text is what gets stored in the log's ``REFLECTION`` block, as
before), and carried alongside as ``Lesson.lesson_json`` so the settlement
pass can persist it as a machine-readable ``REFLECTION_JSON`` block.

If the model ignores the JSON instruction, the free prose is stored as-is
(``lesson_json`` is ``None``) — the legacy behavior, kept as a fallback.
"""

import json
import re
from typing import Any

#: Stable key names of the structured lesson. Downstream readers
#: (REFLECTION_JSON block, trigger/retrieval tooling) rely on them.
LESSON_KEYS = ("these_claim", "vorhersage", "outcome_evidenz", "lektion",
               "konfidenz", "horizonte")

_WORD_CONFIDENCE = {
    "high": 0.8, "hoch": 0.8,
    "medium": 0.5, "mittlere": 0.5, "mittel": 0.5,
    "low": 0.2, "niedrig": 0.2,
}

_JSON_INSTRUCTION = (
    "Answer with a single JSON object and nothing else — no markdown, no code "
    "fences, no commentary. Use exactly these keys:\n"
    '  "these_claim" (string), "vorhersage" (string), "outcome_evidenz" (string),\n'
    '  "lektion" (string), "konfidenz" (number 0.0-1.0),\n'
    '  "horizonte" (list of the window sizes in days the lesson is based on).\n'
    "Keep it compact: at most two sentences per field, at most ~80 words in total."
)


class Lesson(str):
    """A stored lesson: the ``str`` IS the rendered lesson text, so every
    caller that expects ``str`` (log storage, prompt injection, tests) keeps
    working unchanged. ``lesson_json`` holds the parsed structured payload
    when the model returned valid JSON, else ``None`` (prose fallback)."""

    lesson_json: dict | None = None

    def __new__(cls, text: str, lesson_json: dict | None = None):
        obj = super().__new__(cls, text)
        obj.lesson_json = lesson_json
        return obj

    @property
    def json(self) -> dict | None:
        return self.lesson_json


def parse_lesson(content: str | None) -> dict | None:
    """Parse the LLM's lesson answer into the structured schema.

    Tolerates code fences and surrounding prose by locating the outermost
    JSON object. Returns a normalised dict holding only ``LESSON_KEYS``
    (``konfidenz`` as float 0..1, ``horizonte`` as sorted list of positive
    ints), or ``None`` when no usable object is present or the lesson itself
    is missing.
    """
    if not content:
        return None
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    lesson: dict = {}
    for key in ("these_claim", "vorhersage", "outcome_evidenz", "lektion"):
        val = data.get(key)
        lesson[key] = " ".join(str(val).split()) if isinstance(val, str) else ""
    if not lesson["lektion"]:
        # The lesson is the point of this block; without it there is no
        # structured lesson to store, so fall back to the raw prose.
        return None

    conf = data.get("konfidenz")
    if isinstance(conf, (int, float)) and not isinstance(conf, bool):
        lesson["konfidenz"] = max(0.0, min(1.0, float(conf)))
    elif isinstance(conf, str) and conf.strip().lower() in _WORD_CONFIDENCE:
        lesson["konfidenz"] = _WORD_CONFIDENCE[conf.strip().lower()]

    horizonte = data.get("horizonte")
    if isinstance(horizonte, (list, tuple)):
        ints = [int(h) for h in horizonte
                if isinstance(h, (int, float)) and not isinstance(h, bool)
                and float(h) == int(float(h)) and int(float(h)) > 0]
        if ints:
            lesson["horizonte"] = sorted(set(ints))
    return lesson


def render_lesson(lesson: dict) -> str:
    """Deterministic compact rendering of a structured lesson.

    This text is stored verbatim in the REFLECTION block and re-read by
    future runs, so it must be stable across versions: no LLM, no rewording.
    """
    lines = []
    if lesson.get("these_claim"):
        lines.append(f"These: {lesson['these_claim']}")
    if lesson.get("vorhersage"):
        lines.append(f"Vorhersage: {lesson['vorhersage']}")
    if lesson.get("outcome_evidenz"):
        lines.append(f"Outcome: {lesson['outcome_evidenz']}")
    if lesson.get("lektion"):
        tail = []
        if isinstance(lesson.get("konfidenz"), (int, float)):
            tail.append(f"Konfidenz: {lesson['konfidenz']:.1f}")
        if lesson.get("horizonte"):
            tail.append("Fenster: " + ", ".join(str(h) for h in lesson["horizonte"]))
        lines.append("Lektion: " + lesson["lektion"]
                     + (f" ({'; '.join(tail)})" if tail else ""))
    return "\n".join(lines)


class Reflector:
    """Handles reflection on trading decisions."""

    def __init__(self, quick_thinking_llm: Any):
        """Initialize the reflector with an LLM."""
        self.quick_thinking_llm = quick_thinking_llm

    def _system_prompt(self, holding_days: int) -> str:
        """Concise prompt for reflect_on_final_decision (settled memory log entries).

        Asks for the structured lesson (JSON, see ``parse_lesson``) compact
        enough to be re-injected into future agent prompts without bloating
        the context window. The window is named because it bounds what the
        outcome can show: a thesis written for months is not disproved by a
        week, and a lesson that ignores the difference is read by later runs
        as an established failure.
        """
        return (
            "You are a trading analyst reviewing your own past decision now that the outcome is known.\n"
            f"The outcome covers {holding_days} trading days from the first close after the analysis date, "
            "which may be shorter than the horizon the decision was written for.\n"
            + _JSON_INSTRUCTION + "\n\n"
            "Fill the fields in this order:\n"
            "  these_claim — what the decision's core thesis was (one sentence);\n"
            "  vorhersage — what the decision predicted (direction, magnitude, target or stop);\n"
            f"  outcome_evidenz — what the {holding_days}-day outcome shows (cite the figure), "
            "and say so plainly there if the window is too short to judge the thesis;\n"
            "  lektion — one concrete lesson for the next similar analysis;\n"
            "  konfidenz — how well the measured outcome supports the lesson;\n"
            "  horizonte — the window in days this lesson is based on.\n\n"
            "Be specific and terse. Your output is stored in a memory log and "
            "re-read by future analysts, so every word must earn its place."
        )

    def reflect_on_final_decision(
        self,
        final_decision: str,
        raw_return: float | None = None,
        alpha_return: float | None = None,
        benchmark_name: str = "SPY",
        holding_days: int = 5,
        settled: dict | None = None,
        configured: list | None = None,
    ) -> Lesson:
        """Reflect on a trading decision and its outcome.

        When ``settled`` (mapping horizon → {raw, alpha, resolution}) is
        given, the multi-horizon prompt is used — the same path regardless
        of window count, since one measured window no longer differs from
        the legacy single-window case. Returns the lesson as a ``Lesson``
        (a ``str`` subclass); ``.lesson_json`` carries the structured
        payload when the model returned valid JSON.
        """
        if settled:
            content = self._reflect_multi_horizon(
                final_decision, settled, configured or [], benchmark_name
            )
        else:
            messages = [
                ("system", self._system_prompt(holding_days)),
                (
                    "human",
                    (
                        f"Final decision:\n{final_decision}\n\n"
                        f"Return over {holding_days} trading days: {raw_return:+.1%}\n"
                        f"Alpha vs {benchmark_name}: {alpha_return:+.1%}\n\n"
                        "Reflect on this outcome. Did the directional call hold up? "
                        "What does this window say about the thesis — and what lesson "
                        "should be applied to the next similar analysis?"
                    ),
                ),
            ]
            content = self.quick_thinking_llm.invoke(messages).content
        return self._to_lesson(content)

    def _to_lesson(self, content: str) -> Lesson:
        """Parse the model's answer into the structured lesson, falling back
        to the raw prose when the model ignored the JSON instruction."""
        lesson = parse_lesson(content)
        if lesson is None:
            return Lesson(content.strip())
        return Lesson(render_lesson(lesson), lesson)

    def _reflect_multi_horizon(self, final_decision: str, settled: dict,
                               configured: list, benchmark_name: str) -> str:
        """Reflect across every measured evaluation window, not just the first.

        Judging a multi-month thesis on one week of alpha turns a scope
        mismatch into a lesson that the call was wrong. Scoring each
        configured horizon as it trades keeps the lesson honest about which
        windows support or undercut the call.
        """
        lines = []
        for h in sorted(settled):
            v = settled[h] or {}
            raw, alpha = v.get("raw"), v.get("alpha")
            if raw is None and alpha is None:
                continue
            lines.append(f"{h}d: return {raw:+.1%}, alpha vs {benchmark_name} {alpha:+.1%}")
        measured = sorted(h for h in settled if (settled[h] or {}).get("raw") is not None)
        pending = [h for h in configured if h not in measured]
        system = (
            "You are a trading analyst reviewing your own past decision across its "
            "evaluation windows, now that some of them have traded.\n"
            + _JSON_INSTRUCTION + "\n\n"
            "Fill the fields in this order:\n"
            "  these_claim — what the decision's core thesis was (one sentence);\n"
            "  vorhersage — what the decision predicted (direction, magnitude, target or stop);\n"
            "  outcome_evidenz — how the directional call played out over the measured "
            "windows (cite the alpha figures, note which windows confirm or undercut "
            "the thesis, and what the short-vs-long pattern suggests);\n"
            "  lektion — one concrete lesson for the next similar decision at these horizons;\n"
            "  konfidenz — how well the measured windows support the lesson;\n"
            "  horizonte — the window sizes in days the lesson is based on (from the measured ones).\n\n"
            "Be specific and terse. Your output is stored in a memory log and re-read by future "
            "analysts, so every word must earn its place."
        )
        human = (
            f"Final decision:\n{final_decision}\n\n"
            "Measured windows so far:\n" + "\n".join(lines) + "\n"
            + (f"Windows not yet measured: {', '.join(str(h) for h in pending)} days.\n" if pending else "")
            + "\nReflect across the measured windows. Which confirm or undercut the call, and what "
              "lesson should be applied to the next similar decision?"
        )
        return self.quick_thinking_llm.invoke([("system", system), ("human", human)]).content
