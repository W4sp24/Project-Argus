# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Argus backend (Windows, onedir).

Build:  pyinstaller desktop/argus-backend.spec --noconfirm --distpath desktop/resources

onedir, not onefile: onefile re-extracts ~800MB to %TEMP% on *every* launch
(20-60s cold start) and leaves orphaned temp dirs when the process is killed.
onedir also keeps torch's DLLs byte-identical between releases, which is what
lets electron-updater's blockmap ship small deltas.

Most of what follows exists because the affected libraries resolve things at
runtime -- through entry points, importlib, or data files -- in ways
PyInstaller's static analysis cannot see. Each of these fails *silently*: the
frozen app starts and answers /health, then dies the first time a user touches
the relevant feature. Do not trim this list without re-running
desktop/tests/smoke_backend.py against the frozen exe.
"""

import os
import sys

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

# PyInstaller resolves relative paths in a spec against the *current working
# directory*, not the spec's location -- so `pathex=[".."]` means different
# things when you run from desktop/ versus from the repo root. That discrepancy
# silently produced a build without `backend.cli` (freeze succeeded, --init
# died at runtime with ModuleNotFoundError). Everything below is anchored to
# SPECPATH so the build is identical from any directory.
SPEC_DIR = os.path.abspath(SPECPATH)  # noqa: F821 - injected by PyInstaller
REPO_ROOT = os.path.dirname(SPEC_DIR)

# collect_submodules() imports the package using the *interpreter's* sys.path,
# not pathex, so `backend` has to be importable while this spec is evaluated --
# otherwise it silently returns nothing.
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

datas = []
binaries = []
hiddenimports = []


def safe_metadata(name):
    """copy_metadata that tolerates an absent optional package."""
    try:
        return copy_metadata(name)
    except Exception:  # noqa: BLE001 - optional extras may not be installed
        return []


def required_metadata(name):
    """copy_metadata that does NOT tolerate an absent package.

    Use this instead of safe_metadata() for anything the build must not ship
    without -- i.e. a connector whose only failure mode, if it silently goes
    missing, is a user clicking Connect against a dependency that was never
    frozen in. safe_metadata() is for genuinely optional packages (mcp,
    pydantic extras, etc.) where absence just means a feature degrades; here
    absence means the release pipeline installed the wrong extras (see
    release.yml's `pip install -e ".[rag,gcal]"`) and that has to fail the
    build, not ship it.
    """
    return copy_metadata(name)


# --- our own runtime data ---------------------------------------------------
# backend/agent/runtime.py and planner.py read PROMPT_PATH via
# Path(__file__).parent, which resolves inside _MEIPASS once frozen; cli.py's
# TEMPLATE_DIR resolves to _MEIPASS/vault-template. Both must land there or
# chat and `--init` fail at runtime with a bare FileNotFoundError.
datas += [
    (os.path.join(REPO_ROOT, "backend", "agent", "prompts"), "backend/agent/prompts"),
    (os.path.join(REPO_ROOT, "vault-template"), "vault-template"),
]

# Argus itself. argus_server.py imports backend.cli / backend.doctor /
# backend.main lazily inside functions, and the app defers most of the rest
# (agent runtime, rag, study, connectors) the same way, so static analysis has
# little to go on. Collecting the whole first-party package is cheap -- it is
# pure Python -- and removes an entire class of "frozen build starts fine then
# ModuleNotFoundErrors on first real use".
hiddenimports += collect_submodules("backend")

# --- uvicorn ---------------------------------------------------------------
# argus_server.py names http/ws/loop explicitly, which is the real fix for
# uvicorn's `auto` runtime importlib. These are belt-and-braces.
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "h11",
    "websockets",
    "websockets.legacy",
]

# --- mcp (the `--mcp-server` stdio bridge) ---------------------------------
# backend/agent/mcp_server.py imports `mcp` lazily *inside* its functions
# (`from mcp.server import Server`, `from mcp import types`, `from
# mcp.server.stdio import stdio_server`), which is exactly the pattern static
# analysis cannot see. collect_submodules("backend") bundles the module itself,
# so without this the frozen exe accepts --mcp-server and then dies with
# ModuleNotFoundError: mcp the moment a client connects.
hiddenimports += collect_submodules("mcp")
datas += safe_metadata("mcp")

# --- fastapi / pydantic ----------------------------------------------------
hiddenimports += collect_submodules("pydantic")
datas += safe_metadata("pydantic") + safe_metadata("fastapi") + safe_metadata("starlette")

# --- keyring ---------------------------------------------------------------
# keyring discovers backends through entry points, which do not survive
# freezing -> NoKeyringError, and every stored secret (gcal, todoist) breaks.
hiddenimports += collect_submodules("keyring.backends")
hiddenimports += [
    "keyring.backends.Windows",
    "win32ctypes.core",
    "win32ctypes.core.cffi",
    "win32ctypes.pywin32",
]
datas += safe_metadata("keyring")

# --- google calendar / todoist clients --------------------------------------
# Both connectors import their client libraries lazily and in-function --
# google_auth_oauthlib inside gcal.py's connect() (backend/connectors/gcal.py:74)
# and todoist_api_python inside todoist.py's list_tasks() (backend/connectors/
# todoist.py:66) -- which is exactly the pattern static analysis cannot see.
# Before this, CI's `pip install -e ".[rag]"` never installed the gcal extra
# at all, so PyInstaller had nothing to collect and the frozen backend shipped
# with neither connector importable; the keyring entry above already stores
# both of their secrets, so this belongs right after it.
#
# required_metadata(), not safe_metadata(): a build missing this metadata is a
# build that shipped without the connector, which must fail loudly here
# rather than pass all 25 smoke checks and reach a user as an inexplicable 501.
hiddenimports += collect_submodules("google_auth_oauthlib")
hiddenimports += collect_submodules("google.auth")
hiddenimports += collect_submodules("google.oauth2")
hiddenimports += collect_submodules("googleapiclient")
hiddenimports += collect_submodules("todoist_api_python")
hiddenimports += ["google.auth.transport.requests"]
datas += collect_data_files("googleapiclient")  # discovery_cache/documents/*.json
datas += (
    required_metadata("google-auth-oauthlib")
    + required_metadata("google-api-python-client")
    + required_metadata("google-auth")
    + required_metadata("todoist-api-python")
)

# --- apscheduler -----------------------------------------------------------
# APScheduler 3.x resolves triggers/executors via pkg_resources entry points.
# Missing -> LookupError: No trigger by the name "cron" -- which fires inside
# the FastAPI lifespan hook, so the whole app fails to start.
hiddenimports += collect_submodules("apscheduler")
hiddenimports += ["pkg_resources"]
datas += safe_metadata("apscheduler")

# --- watchdog --------------------------------------------------------------
hiddenimports += [
    "watchdog.observers.read_directory_changes",
    "watchdog.observers.winapi",
]

# --- chromadb --------------------------------------------------------------
# The migrations/**/*.sql files are mandatory: chroma runs its schema
# migrations from them on first PersistentClient() and without them you get
# "sqlite3.OperationalError: no such table: collections".
datas += collect_data_files("chromadb", include_py_files=False)
binaries += collect_dynamic_libs("chromadb")
binaries += collect_dynamic_libs("chromadb_rust_bindings")
hiddenimports += [
    "chromadb.telemetry.product.posthog",
    "chromadb.api.segment",
    "chromadb.api.rust",
    "chromadb.segment.impl.manager.local",
    "chromadb.execution.executor.local",
]
datas += safe_metadata("chromadb")

# --- onnxruntime (chroma's default embedding backend) ----------------------
binaries += collect_dynamic_libs("onnxruntime")
hiddenimports += [
    "onnxruntime.capi._pybind_state",
    "onnxruntime.capi.onnxruntime_pybind11_state",
]
datas += safe_metadata("onnxruntime")

# --- sentence-transformers / transformers ----------------------------------
# ST version-probes a long list of packages at import time; a single missing
# dist-info raises PackageNotFoundError. This is the most common ST+PyInstaller
# failure by a wide margin.
for _pkg in (
    "sentence-transformers",
    "transformers",
    "tokenizers",
    "torch",
    "numpy",
    "tqdm",
    "regex",
    "requests",
    "packaging",
    "filelock",
    "pyyaml",
    "huggingface-hub",
    "safetensors",
    "scikit-learn",
    "scipy",
    "Pillow",
):
    datas += safe_metadata(_pkg)

datas += collect_data_files("sentence_transformers")
datas += collect_data_files("transformers", include_py_files=False)
# bge-small-en-v1.5 is a BERT; transformers lazy-imports the model module.
hiddenimports += collect_submodules("transformers.models.bert")
binaries += collect_dynamic_libs("tokenizers")

# --- torch -----------------------------------------------------------------
# Never collect_all('torch'): it drags in include/, test/, torchgen/ and .lib
# import libraries -- +200MB of dead weight and a 10-minute analysis.
binaries += collect_dynamic_libs("torch")

# --- document extraction ---------------------------------------------------
datas += collect_data_files("pdfminer")  # cmap tables; without them CJK PDFs raise
datas += collect_data_files("pptx")  # default template
datas += collect_data_files("docx")  # default template

# --- claude-agent-sdk ------------------------------------------------------
datas += collect_data_files("claude_agent_sdk")
datas += safe_metadata("claude-agent-sdk")

# --- fsrs ------------------------------------------------------------------
datas += safe_metadata("fsrs")


excludes = [
    "tkinter",
    "matplotlib",
    "pandas",
    "IPython",
    "jupyter",
    "notebook",
    "tensorflow",
    "flax",
    "jax",
    "jaxlib",
    "keras",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    "torch.distributed",
    "torch.testing",
    "torchgen",
    "torch.utils.tensorboard",
    "pytest",
]


a = Analysis(
    [os.path.join(SPEC_DIR, "backend", "argus_server.py")],
    # Repo root on sys.path so the `backend` package is importable. The lazy
    # `from backend.cli import ...` inside _cmd_init is only reachable if this
    # is right, and getting it wrong fails at runtime, not at build time.
    pathex=[REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="argus-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX corrupts torch_cpu.dll and the onnxruntime DLLs - hard crash on load.
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="backend",
)
