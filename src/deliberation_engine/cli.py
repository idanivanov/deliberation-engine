"""Command-line interface: loads the inputs, renders the deliberation and saves the transcript."""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pydantic_ai
from dotenv import load_dotenv
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text

from .agents import build_agents
from .config import Inputs, Settings, clean
from .document import DecisionDocument, format_delta, termination_label
from .orchestrator import (
    CHAT_FILE,
    DOCUMENT_JSON,
    DOCUMENT_MD,
    Deliberation,
    Event,
    RoundFinished,
    Turn,
    TurnFinished,
    TurnStarted,
    internal_steps,
)
from .schemas import Confidence, CriticReview, ProposerTurn, RoundMetrics, Summary

COLORS = {"proposer": "cyan", "critic": "magenta", "summarizer": "green"}
ACTIVITY = {
    "proposer": "working on the proposal",
    "critic": "reviewing the proposal",
    "summarizer": "writing the decision document",
}
ACTION_STYLES = {"defended": "bold blue", "revised": "bold yellow", "conceded": "bold red"}
SEVERITY_STYLES = {"blocking": "bold red", "major": "bold yellow", "minor": "dim"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deliberation-engine",
        description="Two agents deliberate over a vague feature request and produce a decision "
        "document. Each input can also come from an environment variable "
        "(DE_SYSTEM_DESCRIPTION, DE_FEATURE_REQUEST).",
    )
    system = parser.add_mutually_exclusive_group()
    system.add_argument("-s", "--system", help="description of the target system, as plain text")
    system.add_argument("--system-file", type=Path, help="read the system description from a file")
    request = parser.add_mutually_exclusive_group()
    request.add_argument("-r", "--request", help="the feature request, as plain text")
    request.add_argument("--request-file", type=Path, help="read the feature request from a file")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show validator retries and model reasoning"
    )
    return parser


def load_inputs(args: argparse.Namespace, settings: Settings) -> Inputs:
    """CLI text or file first, then the environment; then strip and cap each input."""
    system = args.system_file.read_text() if args.system_file else args.system
    request = args.request_file.read_text() if args.request_file else args.request
    system = system if system is not None else settings.system_description
    request = request if request is not None else settings.feature_request
    missing = []
    if not system:
        missing.append("system description (-s/--system, --system-file or DE_SYSTEM_DESCRIPTION)")
    if not request:
        missing.append("feature request (-r/--request, --request-file or DE_FEATURE_REQUEST)")
    if missing:
        raise ValueError("missing input: " + "; ".join(missing))
    return Inputs(clean(system, "system description"), clean(request, "feature request"))


def counted(items: list, one: str, many: str | None = None) -> str:
    return f"{len(items)} {one if len(items) == 1 else many or one + 's'}"


def section(title: str, rows: list[tuple[str, RenderableType]]) -> RenderableType:
    grid = Table.grid(padding=(0, 1))
    grid.add_column(style="bold", no_wrap=True)
    grid.add_column()
    for row in rows:
        # Text renders model output literally; a plain str would be parsed as rich markup.
        grid.add_row(*(Text(cell) if isinstance(cell, str) else cell for cell in row))
    return Group(Text(title, style="underline"), grid if rows else Text("none", style="dim"), "")


def confidence_text(confidence: Confidence) -> Text:
    return Text.assemble(
        ("Confidence ", "bold"), (str(confidence.score), "bold"), " · ", confidence.rationale
    )


def proposer_body(turn: ProposerTurn) -> RenderableType:
    p = turn.proposal
    blocks = []
    if turn.replies:
        rows = [
            (r.challenge_id, Text.assemble((r.action, ACTION_STYLES[r.action]), " ", r.reason))
            for r in turn.replies
        ]
        blocks.append(section("Replies", rows))
    decisions = [
        (d.id, Text.assemble((d.status, "bold"), " ", d.decision, (f" · {d.reason}", "dim")))
        for d in p.key_decisions
    ]
    questions = [
        (q.id, Text.assemble(q.question, (f"\ndefault: {q.default}", "dim")))
        for q in p.open_questions
    ]
    blocks += [
        section("In scope", [(i.id, i.text) for i in p.in_scope]),
        section("Out of scope", [(i.id, i.text) for i in p.out_of_scope]),
        section("Assumptions", [(i.id, i.text) for i in p.assumptions]),
        section("Key decisions", decisions),
        section("Success criteria", [(i.id, i.text) for i in p.success_criteria]),
        section("Open questions", questions),
        confidence_text(turn.confidence),
    ]
    return Group(*blocks)


def summary_body(summary: Summary) -> RenderableType:
    return section(
        "Decision rationales", [(r.decision_id, r.rationale) for r in summary.rationales]
    )


def metrics_line(m: RoundMetrics) -> Text:
    return Text.assemble(
        (f"Round {m.round}", "bold"),
        f" · disagreement {m.disagreement} (Δ {format_delta(m.delta)})",
        f" · confidence gap {m.confidence_gap}",
        f" · resolved {m.resolved}",
        f" · replies {m.defended} defended / {m.revised} revised / {m.conceded} conceded",
    )


def usage_table(turns: list[Turn]) -> Table:
    table = Table(title="Usage", title_justify="left", title_style="bold")
    table.add_column("Agent")
    for column in ("Requests", "Input tokens", "Output tokens", "Cost", "Time"):
        table.add_column(column, justify="right")
    rows = [(agent.upper(), [t for t in turns if t.agent == agent]) for agent in COLORS]
    for name, group in [*rows, ("Total", turns)]:
        costs = [t.usage.cost for t in group if t.usage.cost is not None]
        table.add_row(
            name,
            str(sum(t.usage.requests for t in group)),
            f"{sum(t.usage.input_tokens for t in group):,}",
            f"{sum(t.usage.output_tokens for t in group):,}",
            f"${sum(costs):.4f}" if costs else "n/a",
            f"{sum(t.usage.seconds for t in group):.1f}s",
            style="bold" if name == "Total" else None,
        )
    return table


class Renderer:
    """Renders the orchestrator's events. The spinner goes to a separate, unrecorded console, so
    the animation stays out of transcript.txt."""

    def __init__(self, console: Console, verbose: bool):
        self.console, self.verbose = console, verbose
        self.spinner_console = Console(stderr=True)
        self.status: Status | None = None
        self.raised: set[str] = set()  # challenge IDs seen so far, to tell new from re-raised

    def __call__(self, event: Event) -> None:
        match event:
            case TurnStarted(agent=agent):
                text = f"[bold {COLORS[agent]}]{agent.upper()}[/] is {ACTIVITY[agent]}…"
                self.status = self.spinner_console.status(text)
                self.status.start()
            case TurnFinished(turn=turn):
                self.stop()
                self.print_turn(turn)
            case RoundFinished(metrics=metrics):
                self.console.print(metrics_line(metrics))
                self.console.rule(style="dim")

    def stop(self) -> None:
        if self.status:
            self.status.stop()
            self.status = None

    def intro(self, inputs: Inputs, models: dict[str, str], run_dir: Path) -> None:
        body = Group(
            Text("Feature request", style="bold"),
            Text(inputs.feature_request),
            "",
            Text("System description", style="bold"),
            Text(inputs.system_description),
            "",
            Text(" · ".join(f"{agent.upper()} {model}" for agent, model in models.items()), "dim"),
            Text(f"Output: {run_dir}", "dim"),
        )
        self.console.print(Panel(body, title="[bold]Deliberation Engine[/]", title_align="left"))

    def print_turn(self, turn: Turn) -> None:
        color = COLORS[turn.agent]
        steps = internal_steps(turn)
        if self.verbose:
            for kind, text in steps:
                title = f"{turn.agent.upper()} · {kind}"
                self.console.print(Panel(Text(text), title=title, title_align="left", style="dim"))
        match turn.output:
            case ProposerTurn() as output:
                body, confidence = proposer_body(output), output.confidence
            case CriticReview() as output:
                body, confidence = self.critic_body(output), output.confidence
            case Summary() as output:
                body, confidence = summary_body(output), None
            case _:
                return
        round_label = "" if turn.agent == "summarizer" else f" · round {turn.round}"
        title = f"[bold {color}]{turn.agent.upper()}[/]{round_label} · {turn.timestamp:%H:%M:%S}"
        notes = [f"confidence {confidence.score}"] if confidence else []
        if retries := sum(kind == "retry" for kind, _ in steps):
            notes.append(f"{retries} {'retry' if retries == 1 else 'retries'}")
        self.console.print(
            Panel(
                body,
                title=title,
                title_align="left",
                subtitle=" · ".join(notes) or None,
                subtitle_align="right",
                border_style=color,
            )
        )

    def critic_body(self, review: CriticReview) -> RenderableType:
        blocks = []
        if review.resolved:
            rows = [
                (r.challenge_id, Text(r.closed_by.replace("_", " "), "green"))
                for r in review.resolved
            ]
            blocks.append(section("Resolved", rows))
        rows = []
        for c in review.challenges:
            label = "re-raised" if c.id in self.raised else "new"
            text = Text.assemble(
                (c.severity, SEVERITY_STYLES[c.severity]),
                (f" · on {c.target} · {label}\n", "dim"),
                c.risk,
                (f"\n→ {c.suggestion}", "italic"),
            )
            rows.append((c.id, text))
        self.raised.update(c.id for c in review.challenges)
        blocks.append(section("Challenges", rows))
        verdict = (
            ("✔ satisfied", "bold green") if review.satisfied else ("✘ not satisfied", "bold red")
        )
        blocks += [
            Text.assemble(verdict, " · ", review.justification),
            confidence_text(review.confidence),
        ]
        return Group(*blocks)

    def finish(self, doc: DecisionDocument, turns: list[Turn], run_dir: Path) -> None:
        h = doc.header
        challenged = sum(a.status == "challenged" for a in doc.assumptions)
        counts = " · ".join(
            [
                f"{len(doc.in_scope)} in scope",
                f"{len(doc.out_of_scope)} out of scope",
                f"{counted(doc.assumptions, 'assumption')} ({challenged} challenged)",
                counted(doc.decision_log, "key decision"),
                counted(doc.success_criteria, "success criterion", "success criteria"),
                counted(doc.open_questions, "open question"),
                counted(doc.unresolved_disagreements, "unresolved disagreement"),
            ]
        )
        body = Group(
            Text(doc.overview),
            "",
            Text(counts, "dim"),
            "",
            Text.assemble(("Decision document: ", "bold"), str(run_dir / DOCUMENT_MD)),
            Text(f"Also: {run_dir / DOCUMENT_JSON}, {run_dir / CHAT_FILE}", "dim"),
        )
        style = "green" if h.termination == "converged" else "yellow"
        title = f"[bold]{termination_label(h.termination, h.rounds)}[/]"
        self.console.print(Panel(body, title=title, title_align="left", border_style=style))
        self.console.print(usage_table(turns))


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    pydantic_ai.BANNER_ENABLED = False  # this program owns its output
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings()
    try:
        inputs = load_inputs(args, settings)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    run_dir = Path("runs") / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    console = Console(record=True)
    renderer = Renderer(console, args.verbose)
    deliberation = Deliberation(build_agents(inputs, settings), inputs, settings, run_dir, renderer)
    renderer.intro(inputs, deliberation.log.models, run_dir)
    try:
        document = asyncio.run(deliberation.run())
        renderer.finish(document, deliberation.turns, run_dir)
    except KeyboardInterrupt:
        renderer.stop()
        console.print(f"[bold red]Interrupted.[/] The conversation so far: {run_dir / CHAT_FILE}")
        sys.exit(130)
    except Exception as error:
        renderer.stop()
        message = f"{error!r}\n\nThe conversation so far: {run_dir / CHAT_FILE}"
        console.print(
            Panel(Text(message), title="[bold]ERROR[/]", title_align="left", border_style="red")
        )
        sys.exit(1)
    finally:
        console.save_text(str(run_dir / "transcript.txt"))
