"""A curated shortlist of local models, matched against the user's machine.

The Ollama library has hundreds of models. Most of them cannot call tools, and
Argus has no no-tools fallback — chat's citations (I6) and the planner's
suggest-then-approve flow (I1) are both enforced through tools — so a model
without tool calling is not "degraded" here, it is broken. **Every entry in
this catalog supports tool calling**, which is why the list is short and
hand-maintained rather than fetched from Ollama's index.

Each entry is then scored against :class:`~backend.agent.hardware.HardwareProfile`
so the answer to "which one should I get?" is a badge, not a research project.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.agent.hardware import HardwareProfile

# Verdicts, in descending order of how well a model suits the machine.
FITS = "fits"
SLOW = "slow"
INSUFFICIENT = "insufficient"
UNKNOWN = "unknown"

# A model needs its weights in memory plus room for context and the OS.
# Measured rules of thumb, deliberately generous — recommending something that
# swaps is worse than recommending nothing.
RAM_OVERHEAD_GB = 2.5
RAM_CONTEXT_FACTOR = 1.4
VRAM_OVERHEAD_GB = 1.0
# At or below this size a CPU-only machine stays interactive, so "no GPU" is
# not a reason to warn.
CPU_COMFORTABLE_GB = 2.5


@dataclass(frozen=True)
class CatalogModel:
    """One installable Ollama model.

    ``size_gb`` is the approximate download size at Ollama's default
    quantization (Q4), which is also roughly the memory the weights occupy.
    """

    name: str
    label: str
    size_gb: float
    parameters: str
    summary: str
    tool_calling: bool = field(default=True)

    @property
    def min_ram_gb(self) -> float:
        return round(self.size_gb * RAM_CONTEXT_FACTOR + RAM_OVERHEAD_GB, 1)

    @property
    def min_vram_gb(self) -> float:
        return round(self.size_gb + VRAM_OVERHEAD_GB, 1)


# Ordered smallest-first so the list reads as a ladder: the top entries run
# almost anywhere, the bottom ones need real hardware.
CATALOG: tuple[CatalogModel, ...] = (
    CatalogModel(
        name="llama3.2:1b",
        label="Llama 3.2 1B",
        size_gb=1.3,
        parameters="1B",
        summary="Smallest option that still calls tools. Runs on almost any laptop.",
    ),
    CatalogModel(
        name="llama3.2:3b",
        label="Llama 3.2 3B",
        size_gb=2.0,
        parameters="3B",
        summary="Good balance for older machines with no dedicated graphics card.",
    ),
    CatalogModel(
        name="mistral:7b",
        label="Mistral 7B",
        size_gb=4.1,
        parameters="7B",
        summary="Solid all-rounder; a common first choice on a mid-range PC.",
    ),
    CatalogModel(
        name="qwen2.5:7b",
        label="Qwen 2.5 7B",
        size_gb=4.7,
        parameters="7B",
        summary="Strong instruction following and reliable tool calls for its size.",
    ),
    CatalogModel(
        name="llama3.1:8b",
        label="Llama 3.1 8B",
        size_gb=4.9,
        parameters="8B",
        summary="Widely used and well tested with tools. A safe default if unsure.",
    ),
    CatalogModel(
        name="mistral-nemo:12b",
        label="Mistral Nemo 12B",
        size_gb=7.1,
        parameters="12B",
        summary="Noticeably better reasoning; wants a graphics card with 8GB+.",
    ),
    CatalogModel(
        name="qwen2.5:14b",
        label="Qwen 2.5 14B",
        size_gb=9.0,
        parameters="14B",
        summary="Closest to hosted quality here, if your hardware can take it.",
    ),
    CatalogModel(
        name="qwen2.5:32b",
        label="Qwen 2.5 32B",
        size_gb=20.0,
        parameters="32B",
        summary="Serious hardware only — a 24GB card, or a lot of patience.",
    ),
)


@dataclass(frozen=True)
class CatalogEntry:
    """A catalog model plus how well it suits this particular machine."""

    model: CatalogModel
    verdict: str
    reason: str


def fit_verdict(model: CatalogModel, profile: HardwareProfile) -> tuple[str, str]:
    """Score one model against a machine, with a reason a human can read."""
    if profile.ram_gb is None:
        return UNKNOWN, "could not detect this machine's memory"

    if profile.ram_gb < model.min_ram_gb:
        return (
            INSUFFICIENT,
            f"needs about {model.min_ram_gb}GB of RAM; this machine has {profile.ram_gb}GB",
        )

    if profile.vram_gb is not None:
        if profile.vram_gb >= model.min_vram_gb:
            gpu = profile.gpu_name or "your graphics card"
            return FITS, f"fits in {gpu}'s {profile.vram_gb}GB of video memory"
        if model.size_gb <= CPU_COMFORTABLE_GB:
            return FITS, "small enough to stay responsive without the graphics card"
        return (
            SLOW,
            f"too big for {profile.vram_gb}GB of video memory, so it runs on the "
            "processor — expect slow replies",
        )

    # No GPU detected: either there is none, or it is a vendor Argus cannot
    # probe. Advise for the CPU-only case, which is the safe assumption.
    if model.size_gb <= CPU_COMFORTABLE_GB:
        return FITS, "small enough to run well on the processor alone"
    return (
        SLOW,
        "no NVIDIA graphics card detected, so this runs on the processor — expect slow replies",
    )


def annotated_catalog(profile: HardwareProfile) -> list[CatalogEntry]:
    """The whole catalog, scored against one machine."""
    entries = []
    for model in CATALOG:
        verdict, reason = fit_verdict(model, profile)
        entries.append(CatalogEntry(model=model, verdict=verdict, reason=reason))
    return entries


def recommended(profile: HardwareProfile) -> CatalogModel | None:
    """The largest model that fits comfortably — Argus's one-click suggestion."""
    fitting = [entry.model for entry in annotated_catalog(profile) if entry.verdict == FITS]
    return max(fitting, key=lambda model: model.size_gb) if fitting else None


def find(name: str) -> CatalogModel | None:
    """Look a catalog model up by its Ollama name."""
    return next((model for model in CATALOG if model.name == name), None)
