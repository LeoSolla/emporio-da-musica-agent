"""Run the behavior evals against the real model and write evals/results.md.

    python evals/run_evals.py            # all cases
    python evals/run_evals.py price_lookup off_topic   # selected cases

Costs a few cents per full run (about 16 short conversations).
"""

from __future__ import annotations

import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))
load_dotenv(ROOT / ".env")

from cases import CASES, Case  # noqa: E402

from emporio_agent.agent import Agent  # noqa: E402
from emporio_agent.data_store import StoreData  # noqa: E402
from emporio_agent.policies import PolicyManual  # noqa: E402
from emporio_agent.tools import ToolBox  # noqa: E402

FIXED_NOW = datetime(2026, 2, 20, 10, 0)  # Friday, store open


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


@dataclass
class Result:
    case: Case
    replies: list[str]
    tools: list[str]
    failures: list[str]
    seconds: float

    @property
    def passed(self) -> bool:
        return not self.failures


def run_case(case: Case, store: StoreData, manual: PolicyManual) -> Result:
    toolbox = ToolBox(store, manual, now=lambda: FIXED_NOW, complaints_file=ROOT / "var" / "eval_complaints.jsonl")
    agent = Agent(toolbox)
    start = time.time()
    replies, tools = [], []
    for text in case.turns:
        turn = agent.chat(text)
        replies.append(turn.reply)
        tools += [c.name for c in turn.tool_calls]

    final, everything = norm(replies[-1]), norm("\n".join(replies))
    failures = []
    for name in sorted(case.tools_called - set(tools)):
        failures.append(f"tool not called: {name}")
    for name in sorted(case.tools_not_called & set(tools)):
        failures.append(f"tool should not be called: {name}")
    for needle in case.contains:
        if norm(needle) not in final:
            failures.append(f"missing: {needle!r}")
    for group in case.any_of:
        if not any(norm(n) in final for n in group):
            failures.append(f"missing one of: {group}")
    for needle in case.not_contains:
        if norm(needle) in everything:
            failures.append(f"must not contain: {needle!r}")
    return Result(case, replies, tools, failures, time.time() - start)


def write_report(results: list[Result], path: Path) -> None:
    passed = sum(r.passed for r in results)
    lines = [
        "# Behavior eval results",
        "",
        f"Run at {datetime.now():%Y-%m-%d %H:%M}; store clock fixed at {FIXED_NOW:%Y-%m-%d %H:%M}.",
        f"**{passed}/{len(results)} cases passed.**",
        "",
        "| Case | Result | Tools called | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        lines.append(f"| `{r.case.id}` | {'pass' if r.passed else 'FAIL'} | {', '.join(r.tools) or '(none)'} | "
                     f"{'; '.join(r.failures)} |")
    lines += ["", "## Conversations", ""]
    for r in results:
        lines.append(f"### {r.case.id}")
        for user, reply in zip(r.case.turns, r.replies):
            lines += ["", f"**Customer:** {user}", "", f"**Agent:** {reply}"]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    selected = set(sys.argv[1:])
    cases = [c for c in CASES if not selected or c.id in selected]
    store, manual = StoreData.load(), PolicyManual.load()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda c: run_case(c, store, manual), cases))

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"{status}  {r.case.id:<24} {r.seconds:5.1f}s  tools={r.tools}")
        for f in r.failures:
            print(f"      - {f}")
    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} passed")
    if not selected:
        write_report(results, ROOT / "evals" / "results.md")
        print("Report: evals/results.md")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
