"""Tests for hardware detection and the local-model fit verdicts.

The verdicts are the whole point of the local picker — they turn "which model
should I download?" into a badge — so they are pinned against synthetic
machines rather than whatever the CI runner happens to have.
"""

from __future__ import annotations

import pytest

from backend.agent.hardware import HardwareProfile, ollama_base_url, parse_nvidia_smi, total_ram_gb
from backend.agent.model_catalog import (
    CATALOG,
    FITS,
    INSUFFICIENT,
    SLOW,
    UNKNOWN,
    annotated_catalog,
    find,
    fit_verdict,
    recommended,
)

THIN_LAPTOP = HardwareProfile(ram_gb=8.0, vram_gb=None)
GAMING_PC = HardwareProfile(ram_gb=32.0, vram_gb=12.0, gpu_name="NVIDIA GeForce RTX 4070")
BIG_RAM_NO_GPU = HardwareProfile(ram_gb=64.0, vram_gb=None)
UNDETECTED = HardwareProfile(ram_gb=None, vram_gb=None)


def model(name: str):
    found = find(name)
    assert found is not None, f"{name} is missing from the catalog"
    return found


# --- catalog integrity ------------------------------------------------------


def test_every_catalog_model_supports_tool_calling() -> None:
    """Argus has no no-tools fallback, so a non-tool model is broken, not degraded."""
    assert CATALOG, "the catalog must not be empty"
    assert all(entry.tool_calling for entry in CATALOG)


def test_catalog_names_are_unique_and_ollama_shaped() -> None:
    names = [entry.name for entry in CATALOG]
    assert len(names) == len(set(names))
    assert all(":" in name for name in names), "an Ollama name carries an explicit tag"


def test_catalog_is_ordered_smallest_first() -> None:
    """The list should read as a ladder, from runs-anywhere to needs-real-hardware."""
    sizes = [entry.size_gb for entry in CATALOG]
    assert sizes == sorted(sizes)


def test_memory_minimums_exceed_the_weights_themselves() -> None:
    for entry in CATALOG:
        assert entry.min_ram_gb > entry.size_gb, "context and the OS need headroom too"
        assert entry.min_vram_gb > entry.size_gb


# --- fit verdicts -----------------------------------------------------------


def test_gpu_machine_fits_models_that_fit_in_vram() -> None:
    verdict, reason = fit_verdict(model("llama3.1:8b"), GAMING_PC)
    assert verdict == FITS
    assert "RTX 4070" in reason, "the reason names the actual card"


def test_gpu_machine_warns_when_a_model_overflows_vram() -> None:
    """32B weights do not fit 12GB, but 32GB of RAM means it still runs — slowly."""
    verdict, reason = fit_verdict(model("qwen2.5:32b"), GAMING_PC)
    assert verdict == SLOW
    assert "slow" in reason


def test_thin_laptop_rejects_models_it_cannot_hold() -> None:
    verdict, reason = fit_verdict(model("qwen2.5:32b"), THIN_LAPTOP)
    assert verdict == INSUFFICIENT
    assert "8.0GB" in reason, "the reason states what the machine actually has"


def test_thin_laptop_still_gets_a_usable_small_model() -> None:
    assert fit_verdict(model("llama3.2:1b"), THIN_LAPTOP)[0] == FITS
    assert fit_verdict(model("llama3.2:3b"), THIN_LAPTOP)[0] == FITS


def test_plenty_of_ram_without_a_gpu_is_slow_not_insufficient() -> None:
    """A 64GB workstation can run a 14B model; it will just be unpleasant."""
    verdict, reason = fit_verdict(model("qwen2.5:14b"), BIG_RAM_NO_GPU)
    assert verdict == SLOW
    assert "processor" in reason


def test_undetectable_hardware_reports_unknown_rather_than_guessing() -> None:
    for entry in CATALOG:
        verdict, _ = fit_verdict(entry, UNDETECTED)
        assert verdict == UNKNOWN


def test_annotated_catalog_covers_every_model() -> None:
    entries = annotated_catalog(GAMING_PC)
    assert len(entries) == len(CATALOG)
    assert all(entry.reason for entry in entries), "every verdict carries a reason"


# --- recommendation ---------------------------------------------------------


def test_recommendation_is_the_largest_comfortable_model() -> None:
    pick = recommended(GAMING_PC)
    assert pick is not None
    assert pick.size_gb <= 12.0, "12GB of VRAM should not pull down the 20GB model"
    assert fit_verdict(pick, GAMING_PC)[0] == FITS


def test_recommendation_on_a_thin_laptop_stays_small() -> None:
    pick = recommended(THIN_LAPTOP)
    assert pick is not None
    assert pick.size_gb <= 2.5


def test_no_recommendation_when_hardware_is_unknown() -> None:
    assert recommended(UNDETECTED) is None


# --- probes -----------------------------------------------------------------


def test_parse_nvidia_smi_reads_name_and_megabytes() -> None:
    parsed = parse_nvidia_smi("NVIDIA GeForce RTX 4070, 12282 MiB")
    assert parsed is not None
    assert parsed[1] == "NVIDIA GeForce RTX 4070"
    assert 11.5 < parsed[0] < 12.5


def test_parse_nvidia_smi_takes_the_largest_single_card() -> None:
    """A model runs on one GPU, so summing would recommend models that cannot load."""
    parsed = parse_nvidia_smi("NVIDIA A, 8192 MiB\nNVIDIA B, 24564 MiB")
    assert parsed is not None
    assert parsed[1] == "NVIDIA B"
    assert parsed[0] > 20


def test_parse_nvidia_smi_ignores_unparseable_output() -> None:
    assert parse_nvidia_smi("") is None
    assert parse_nvidia_smi("command not found") is None
    assert parse_nvidia_smi("GPU, not-a-number") is None


def test_total_ram_is_detected_on_this_machine() -> None:
    """Windows/Linux/macOS all have a path; a None here means the probe regressed."""
    ram = total_ram_gb()
    assert ram is not None
    assert 0.5 < ram < 4096


def test_ollama_base_url_honours_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ollama_base_url() == "http://localhost:11434"

    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:22222")
    assert ollama_base_url() == "http://127.0.0.1:22222"

    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.example.com/")
    assert ollama_base_url() == "https://ollama.example.com"
