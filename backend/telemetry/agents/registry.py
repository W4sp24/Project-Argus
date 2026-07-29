"""The set of agent sources Argus knows how to read, and the report over them.

``GET /api/usage/agents`` is the one caller. Every source is scanned and
reported on every request, including sources that are not installed: an agent
that vanishes from the response looks like a bug, whereas one that says
``detected: false`` with an install hint says something true. The selector
renders those dimmed and inert with the hint printed on the row, so nothing is
hidden behind a click that cannot be made.

Sources are no longer a hardcoded list. Built-ins come first, then whatever the
user registered in ``.argus/agent-sources.json`` — see
:mod:`backend.telemetry.agents.store`.
"""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from backend.core.config import Settings
from backend.telemetry import scan
from backend.telemetry.agents.base import AgentSource, GenericSource
from backend.telemetry.agents.claude_code import ClaudeCodeSource, ClaudeSubagentSource
from backend.telemetry.agents.codex import CodexSource
from backend.telemetry.agents.store import load_sources

#: Order is the order the selector lists them in.
BUILTIN_SOURCES: list[AgentSource] = [
    ClaudeCodeSource(),
    ClaudeSubagentSource(),
    CodexSource(),
]

#: Synthetic id for the combined view — never stored in ``cli_usage.agent``.
COMBINED_ID = "all"


def all_sources(settings: Settings | None = None) -> list[AgentSource]:
    """Built-in sources, then the user's own.

    A malformed custom entry is skipped rather than fatal: one bad record in
    the registry must not take the whole usage panel down with it.
    """
    sources: list[AgentSource] = list(BUILTIN_SOURCES)
    if settings is None:
        return sources
    known = {source.id for source in sources}
    for entry in load_sources(settings.agent_sources_file):
        try:
            source = GenericSource(entry)
        except Exception:  # noqa: BLE001 - a bad record is skipped, not fatal
            continue
        if source.id in known:
            continue
        known.add(source.id)
        sources.append(source)
    return sources


class AgentModelUsage(BaseModel):
    """Per-model totals within one agent, priced by that agent's rate table."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    #: True when Argus has no published rate — the cost above is a floor, not a bill.
    unpriced: bool


class AgentUsage(BaseModel):
    """One agent's slice of the report (or the combined view, id ``all``)."""

    id: str
    label: str
    #: Whether this agent is installed locally. False still returns zeroes, not an error.
    detected: bool
    install_hint: str
    #: False for user-registered sources — only those offer a delete action.
    builtin: bool = True
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    #: Total for the equivalent window immediately before this one; None for ``all``.
    previous_total_tokens: int | None
    series: list[scan.UsagePoint]
    models: list[AgentModelUsage]
    #: Models that ran but have no published rate, named rather than guessed at.
    unpriced_models: list[str]
    #: Set when transcripts exist but none of them parsed into a single row.
    #:
    #: Zero is otherwise indistinguishable from "we cannot read this format",
    #: and the parsers degrade to silently skipping lines they do not
    #: recognise. Codex in particular has never been validated against a real
    #: ``~/.codex`` — its tests are fixtures written from the same assumption as
    #: its parser — so a wrong guess about the format would show a confident
    #: zero forever. This makes that case say so.
    unreadable: str | None = None


class AgentsUsageReport(BaseModel):
    """``GET /api/usage/agents`` payload.

    ``agents`` always has one entry per known source, installed or not.
    ``combined`` sums every agent, so the panel's "ALL" tab is a real total
    rather than something the client re-derives and gets subtly wrong.
    """

    range: scan.CliRange
    agents: list[AgentUsage]
    combined: AgentUsage


def sync_all(
    conn: sqlite3.Connection, sources: list[AgentSource] | None = None
) -> dict[str, int]:
    """Ingest new transcripts for every installed source. Never raises.

    One source failing must not cost the others their scan, so each is guarded
    independently — the panel degrades to stale numbers for that agent alone.
    """
    inserted: dict[str, int] = {}
    for source in sources if sources is not None else BUILTIN_SOURCES:
        try:
            if not source.detect():
                inserted[source.id] = 0
                continue
            inserted[source.id] = scan.sync_rows(
                conn, source.id, source.transcripts(), source.parse
            )
        except Exception:  # noqa: BLE001 - a usage scan never breaks the page
            inserted[source.id] = 0
    return inserted


def _summarise(
    conn: sqlite3.Connection,
    range_: scan.CliRange,
    rows: list[sqlite3.Row],
    *,
    id_: str,
    label: str,
    detected: bool,
    install_hint: str,
    builtin: bool,
    rate_lookup: dict[str, AgentSource],
    agent_filter: str | None,
    unreadable: str | None = None,
) -> AgentUsage:
    """Fold ``rows`` into one report slice, pricing each model by its own agent."""
    totals = [0, 0, 0, 0]
    for row in rows:
        for index, counter in enumerate(scan.COUNTERS):
            totals[index] += int(row[counter] or 0)

    # Combined view mixes agents, so a model is priced by the agent that ran it
    # rather than by whichever table happens to be first.
    per_model: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        entry = per_model.setdefault((row["agent"], row["model"]), [0, 0, 0, 0])
        for index, counter in enumerate(scan.COUNTERS):
            entry[index] += int(row[counter] or 0)

    # Priced per (agent, model) — the same model name under two agents may have
    # two different rate tables — but *reported* per model. In the combined view
    # the question is "how much gpt-5 did I use", not "gpt-5 via each source",
    # and one row per pair would list the same model repeatedly.
    merged: dict[str, dict] = {}
    unpriced: set[str] = set()
    cost = 0.0
    for (agent_id, model), counts in per_model.items():
        source = rate_lookup.get(agent_id)
        priced = source.rate_for(model) is not None if source else False
        model_cost = source.cost(model, counts[0], counts[1]) if source else 0.0
        cost += model_cost
        if not priced:
            # Named even when another agent *could* price this model: the figure
            # shown is then missing this slice, and saying so beats implying a
            # complete total.
            unpriced.add(model)
        entry = merged.setdefault(model, {"counts": [0, 0, 0, 0], "cost": 0.0, "priced": False})
        for index in range(4):
            entry["counts"][index] += counts[index]
        entry["cost"] += model_cost
        entry["priced"] = entry["priced"] or priced

    models = [
        AgentModelUsage(
            model=model,
            input_tokens=entry["counts"][0],
            output_tokens=entry["counts"][1],
            cache_creation_input_tokens=entry["counts"][2],
            cache_read_input_tokens=entry["counts"][3],
            total_tokens=sum(entry["counts"]),
            estimated_cost_usd=round(entry["cost"], 4),
            unpriced=not entry["priced"],
        )
        for model, entry in sorted(merged.items(), key=lambda item: -sum(item[1]["counts"]))
    ]

    return AgentUsage(
        id=id_,
        label=label,
        detected=detected,
        install_hint=install_hint,
        builtin=builtin,
        input_tokens=totals[0],
        output_tokens=totals[1],
        cache_creation_input_tokens=totals[2],
        cache_read_input_tokens=totals[3],
        total_tokens=sum(totals),
        estimated_cost_usd=round(cost, 4),
        previous_total_tokens=scan.previous_total(conn, range_, agent_filter),
        series=scan.series(rows, range_),
        models=models,
        unpriced_models=sorted(unpriced),
        unreadable=unreadable,
    )


def _unreadable_reason(conn: sqlite3.Connection, source: AgentSource) -> str | None:
    """"We found its logs and understood none of them", or None if all is well.

    Checked against the whole table rather than the selected range: a user who
    has not run Codex this week should see an honest zero, not a warning. Only
    transcripts that produced *no rows ever* mean the format is wrong.
    """
    try:
        if not source.detect():
            return None
        found = sum(1 for _ in source.transcripts())
        if not found:
            return None
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM cli_usage WHERE agent = ?", (source.id,)
        ).fetchone()
        if row and int(row["n"]) > 0:
            return None
        return (
            f"found {found} transcript file(s) under {source.root()} but could not read "
            "usage from any of them — the log format may have changed"
        )
    except Exception:  # noqa: BLE001 - a diagnostic must never break the page
        return None


def agents_report(
    conn: sqlite3.Connection, range_: scan.CliRange, settings: Settings | None = None
) -> AgentsUsageReport:
    """Scan every source, then aggregate one slice per agent plus the total."""
    sources = all_sources(settings)
    sync_all(conn, sources)
    lookup = {source.id: source for source in sources}
    all_rows = scan.fetch_rows(conn, range_)

    agents = [
        _summarise(
            conn,
            range_,
            [row for row in all_rows if row["agent"] == source.id],
            id_=source.id,
            label=source.label,
            detected=source.detect(),
            install_hint=source.install_hint,
            builtin=source.builtin,
            rate_lookup=lookup,
            agent_filter=source.id,
            unreadable=_unreadable_reason(conn, source),
        )
        for source in sources
    ]

    # Rows whose agent is no longer registered would otherwise sit in the
    # combined total with nothing in the selector accounting for them, so the
    # parts would visibly fail to add up to the whole.
    known = {source.id for source in sources}
    combined_rows = [row for row in all_rows if row["agent"] in known]

    combined = _summarise(
        conn,
        range_,
        combined_rows,
        id_=COMBINED_ID,
        label="All agents",
        detected=any(agent.detected for agent in agents),
        install_hint="Install Claude Code or the Codex CLI to see usage here.",
        builtin=True,
        rate_lookup=lookup,
        agent_filter=None,
    )

    return AgentsUsageReport(range=range_, agents=agents, combined=combined)
