"""What this machine can actually run.

Local inference needs *more* horsepower than a hosted call, not less, so
"just run it locally" is bad advice on a thin laptop. This module measures RAM
and (where detectable) GPU VRAM so :mod:`backend.agent.model_catalog` can say
which models fit rather than leaving the user to guess and then wait twenty
minutes for a download that swaps to death.

Detection is deliberately conservative. VRAM is vendor-specific and there is no
portable API; ``nvidia-smi`` is reliable on Windows, which is Argus's primary
target, and everything else reports ``None`` — *unknown*, not zero. Unknown
means the catalog falls back to CPU-safe advice instead of pretending to know.
No new dependency: RAM comes from the platform's own interface via stdlib.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

BYTES_PER_GB = 1024**3
PROBE_TIMEOUT_SECONDS = 5
OLLAMA_DEFAULT_URL = "http://localhost:11434"


@dataclass(frozen=True)
class HardwareProfile:
    """What Argus could detect. ``None`` always means "unknown", never "none"."""

    ram_gb: float | None = None
    vram_gb: float | None = None
    gpu_name: str | None = None
    platform: str = sys.platform

    @property
    def gpu_detected(self) -> bool:
        return self.vram_gb is not None


def _run(command: list[str]) -> str | None:
    """Run a probe command, returning stdout or None if it is unavailable."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            # Without this a console window flashes on every probe in the
            # packaged Windows app.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def total_ram_gb() -> float | None:
    """Total physical RAM in GB, or None when it cannot be determined."""
    if os.name == "nt":
        return _windows_ram_gb()
    try:  # Linux, and any POSIX exposing these
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return round(pages * page_size / BYTES_PER_GB, 1)
    except (AttributeError, ValueError, OSError):
        pass
    if sys.platform == "darwin":  # macOS has no SC_PHYS_PAGES
        output = _run(["sysctl", "-n", "hw.memsize"])
        if output and output.isdigit():
            return round(int(output) / BYTES_PER_GB, 1)
    return None


def _windows_ram_gb() -> float | None:
    """Total RAM via GlobalMemoryStatusEx — stdlib ctypes, no psutil."""
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return round(status.ullTotalPhys / BYTES_PER_GB, 1)
    except Exception:  # noqa: BLE001 - a failed probe is "unknown", not fatal
        return None


def parse_nvidia_smi(output: str) -> tuple[float, str] | None:
    """Parse ``nvidia-smi`` CSV output into (VRAM GB, GPU name).

    Multi-GPU machines report one line per card; the largest single card is
    what matters, because a model runs on one GPU rather than across all of
    them, so summing would recommend models that cannot load.
    """
    best: tuple[float, str] | None = None
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        name, raw_memory = parts[0], parts[1]
        digits = "".join(ch for ch in raw_memory if ch.isdigit())
        if not digits:
            continue
        gb = round(int(digits) / 1024, 1)  # nvidia-smi reports MiB
        if best is None or gb > best[0]:
            best = (gb, name)
    return best


def gpu_vram_gb() -> tuple[float | None, str | None]:
    """(VRAM GB, GPU name) for the largest NVIDIA card, else (None, None).

    Non-NVIDIA GPUs (AMD, Intel Arc, Apple Silicon's unified memory) are not
    detected. That is a known gap, flagged as a spike in the design doc — they
    report as unknown, which yields CPU-safe recommendations rather than wrong
    ones.
    """
    output = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if not output:
        return None, None
    parsed = parse_nvidia_smi(output)
    if parsed is None:
        return None, None
    return parsed[0], parsed[1]


def detect() -> HardwareProfile:
    """Probe this machine. Never raises — undetectable values are None."""
    vram, gpu = gpu_vram_gb()
    return HardwareProfile(ram_gb=total_ram_gb(), vram_gb=vram, gpu_name=gpu)


# --- Ollama ------------------------------------------------------------------


def ollama_base_url() -> str:
    """Where Ollama listens (``OLLAMA_HOST`` when set, else the default)."""
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if not host:
        return OLLAMA_DEFAULT_URL
    if host.startswith(("http://", "https://")):
        return host.rstrip("/")
    return f"http://{host.rstrip('/')}"


def ollama_models_dir() -> str:
    """Where Ollama stores models.

    Reported read-only, on purpose: Ollama owns this location via
    ``OLLAMA_MODELS`` and ``ollama pull`` has no per-call destination flag, so
    Argus surfaces the real path rather than promising a per-model choice that
    Ollama cannot honor.
    """
    configured = os.environ.get("OLLAMA_MODELS", "").strip()
    if configured:
        return configured
    home = os.path.expanduser("~")
    return os.path.join(home, ".ollama", "models")


def ollama_available() -> bool:
    """True when the ``ollama`` CLI is on PATH."""
    import shutil

    return shutil.which("ollama") is not None


async def ollama_reachable(timeout: float = 3.0) -> bool:
    """True when an Ollama server answers on its API port."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{ollama_base_url()}/api/tags")
        return response.status_code < 400
    except Exception:  # noqa: BLE001 - not running is the expected case
        return False
