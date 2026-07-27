"""The set of agent sources Argus knows how to read, and the report over them.

``GET /api/usage/agents`` is the one caller. Every source is scanned and
reported on every request, including sources that are not installed: an agent
that vanishes from the response looks like a bug, whereas one that says
``detected: false`` with an install hint says something true.
"""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from backend.telemetry import scan
from backend.telemetry.agents.base import AgentSource
from backend.telemetry.agents.claude_code import ClaudeCodeSource
from backend.telemetry.agents.codex import CodexSource

#: Order is the order the UI renders tabs in.
SOURCES: list[AgentSource] = [ClaudeCodeSource(), CodexSource()]

#: Synthetic id for the combined view — never stored in ``cli_usage.agent``.
COMBINED_ID = "all"


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


class AgentsUsageReport(BaseModel):
    """``GET /api/usage/agents`` payload.

    ``agents`` always has one entry per known source, installed or not.
    ``combined`` sums every agent, so the panel's "ALL" tab is a real total
    rather than something the client re-derives and gets subtly wrong.
    """

    range: scan.CliRange
    agents: list[AgentUsage]
    combined: AgentUsage


def sync_all(conn: sqlite3.Connection) -> dict[str, int]:
    """Ingest new transcripts for every installed source. Never raises.

    One source failing must not cost the others their scan, so each is guarded
    independently — the panel degrades to stale numbers for that agent alone.
    """
    inserted: dict[str, int] = {}
    for source in SOURCES:
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
    rate_lookup: dict[str, AgentSource],
    agent_filter: str | None,
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

    models: list[AgentModelUsage] = []
    unpriced: set[str] = set()
    cost = 0.0
    for (agent_id, model), counts in sorted(per_model.items(), key=lambda item: -sum(item[1])):
        source = rate_lookup.get(agent_id)
        priced = source.rate_for(model) is not None if source else False
        model_cost = source.cost(model, counts[0], counts[1]) if source else 0.0
        cost += model_cost
        if not priced:
            unpriced.add(model)
        models.append(
            AgentModelUsage(
                model=model,
                input_tokens=counts[0],
                output_tokens=counts[1],
                cache_creation_input_tokens=counts[2],
                cache_read_input_tokens=counts[3],
                total_tokens=sum(counts),
                estimated_cost_usd=round(model_cost, 4),
                unpriced=not priced,
            )
        )

    return AgentUsage(
        id=id_,
        label=label,
        detected=detected,
        install_hint=install_hint,
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
    )


def agents_report(conn: sqlite3.Connection, range_: scan.CliRange) -> AgentsUsageReport:
    """Scan every source, then aggregate one slice per agent plus the total."""
    sync_all(conn)
    lookup = {source.id: source for source in SOURCES}
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
            rate_lookup=lookup,
            agent_filter=source.id,
        )
        for source in SOURCES
    ]

    combined = _summarise(
        conn,
        range_,
        all_rows,
        id_=COMBINED_ID,
        label="All agents",
        detected=any(agent.detected for agent in agents),
        install_hint="Install Claude Code or the Codex CLI to see usage here.",
        rate_lookup=lookup,
        agent_filter=None,
    )

    return AgentsUsageReport(range=range_, agents=agents, combined=combined)
