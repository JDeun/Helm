"""Environment discovery for Helm.

Discovers runtime fingerprint, hardware profile, model provider state,
and helm intelligence state. Read-only — never mutates configs.
"""
from __future__ import annotations

import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import detect_layout
from scripts.model_provider_probe import (
    ProviderProbe,
    probe_api_providers_from_env,
    probe_local_providers,
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

RuntimeKind = Literal["helm", "openclaw", "hermes", "generic", "unknown"]
RuntimeModelMode = Literal["api_only", "local_only", "hybrid", "unavailable", "unknown"]
RuntimeReadiness = Literal["ready", "degraded", "unknown"]
HelmIntelligenceMode = Literal[
    "deterministic_only",
    "optional_cloud_available",
    "optional_local_available",
    "optional_hybrid_available",
]
RuntimePriority = str  # e.g. "api_first", "local_first", "runtime_defined"


@dataclass(frozen=True)
class RuntimeFingerprint:
    kind: RuntimeKind
    confidence: float
    adapter: str
    root: str
    markers: list[str]
    reasons: list[str]


class GpuInfo(NamedTuple):
    name: str
    vram_gb: float | None
    vendor: str  # "nvidia", "amd", "apple"


@dataclass(frozen=True)
class HardwareProfile:
    os_name: str
    machine: str
    processor: str | None
    is_macos: bool
    is_apple_silicon: bool
    memory_total_gb: float | None
    low_ram: bool
    python_version: str
    gpu_detected: bool = False
    gpu_name: str | None = None
    vram_gb: float | None = None
    gpus: tuple[GpuInfo, ...] = ()


@dataclass(frozen=True)
class RuntimeModelState:
    runtime_model_detected: bool
    mode: RuntimeModelMode
    priority: str
    api_candidates: list[dict]
    local_candidates: list[dict]
    primary_candidate: str | None
    fallback_candidates: list[str]
    source: str
    readiness: RuntimeReadiness
    note: str | None = None


@dataclass(frozen=True)
class HelmIntelligenceState:
    mode: HelmIntelligenceMode
    cloud_calls_enabled: bool
    local_model_calls_enabled: bool
    reason: str


@dataclass(frozen=True)
class DiscoverySnapshot:
    runtime: RuntimeFingerprint
    hardware: HardwareProfile
    runtime_model_state: RuntimeModelState
    helm_intelligence_state: HelmIntelligenceState
    strategy: dict[str, object]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Runtime fingerprint detection
# ---------------------------------------------------------------------------

_KIND_CONFIDENCE: dict[str, float] = {
    "helm": 0.95,
    "openclaw": 0.85,
    "hermes": 0.80,
    "generic": 0.50,
    "unknown": 0.10,
}

_KIND_ADAPTER: dict[str, str] = {
    "helm": "helm_native",
    "openclaw": "openclaw_compat",
    "hermes": "hermes_compat",
    "generic": "generic",
    "unknown": "none",
}


def _detect_runtime(workspace: Path) -> RuntimeFingerprint:
    layout = detect_layout(workspace)
    kind: RuntimeKind = layout.kind if layout.kind in _KIND_CONFIDENCE else "unknown"  # type: ignore[assignment]
    confidence = _KIND_CONFIDENCE.get(kind, 0.10)
    adapter = _KIND_ADAPTER.get(kind, "none")
    markers = list(layout.markers)
    reasons = [f"marker: {m}" for m in markers] if markers else ["no markers found"]

    return RuntimeFingerprint(
        kind=kind,
        confidence=confidence,
        adapter=adapter,
        root=str(layout.root),
        markers=markers,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

def _read_memory_total_gb() -> float | None:
    """Cross-platform memory detection. Returns None if unavailable."""
    system = platform.system()

    if system == "Darwin":
        try:
            import subprocess
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                return int(result.stdout.strip()) / (1024 ** 3)
        except Exception:  # noqa: BLE001
            pass
        return None

    if system == "Linux":
        try:
            meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
            for line in meminfo.splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / (1024 ** 2)
        except Exception:  # noqa: BLE001
            pass
        return None

    if system == "Windows":
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return stat.ullTotalPhys / (1024 ** 3)
        except Exception:  # noqa: BLE001
            pass
        return None

    # Fallback: try psutil
    try:
        import psutil  # type: ignore[import]
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:  # noqa: BLE001
        return None


_LOW_RAM_THRESHOLD_GB = 17.0


def _detect_gpu() -> tuple[bool, list[GpuInfo]]:
    """Detect GPUs. Returns (detected, gpu_list)."""
    import subprocess

    gpus: list[GpuInfo] = []

    # --- NVIDIA via nvidia-smi ---
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                name = parts[0] if parts else "NVIDIA GPU"
                vram: float | None = None
                if len(parts) > 1:
                    try:
                        vram = float(parts[1]) / 1024.0  # MiB to GiB
                    except (ValueError, IndexError):
                        pass
                gpus.append(GpuInfo(name=name, vram_gb=vram, vendor="nvidia"))
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    # --- AMD via rocm-smi ---
    try:
        result = subprocess.run(
            ["rocm-smi", "--showproductname", "--showmeminfo", "vram"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            _parse_rocm_smi(result.stdout, gpus)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    return (len(gpus) > 0, gpus)


def _parse_rocm_smi(output: str, gpus: list[GpuInfo]) -> None:
    """Parse rocm-smi output for GPU names and VRAM."""
    gpu_names: dict[int, str] = {}
    gpu_vram: dict[int, float | None] = {}

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        # Pattern: "GPU[N]\t\t: Card Series:\t\tName"
        if "Card Series:" in line or "Card series:" in line:
            try:
                idx = int(line.split("GPU[")[1].split("]")[0])
                name = line.split(":")[-1].strip()
                gpu_names[idx] = name
            except (IndexError, ValueError):
                pass
        # Pattern: "GPU[N]\t\t: VRAM Total Memory (B):\t\tBytes"
        if "VRAM Total Memory" in line or "vram total" in line.lower():
            try:
                idx = int(line.split("GPU[")[1].split("]")[0])
                val = line.split(":")[-1].strip()
                gpu_vram[idx] = float(val) / (1024 ** 3)  # bytes to GiB
            except (IndexError, ValueError):
                pass

    for idx in sorted(gpu_names.keys()):
        gpus.append(GpuInfo(
            name=gpu_names[idx],
            vram_gb=gpu_vram.get(idx),
            vendor="amd",
        ))


def _detect_hardware() -> HardwareProfile:
    os_name = platform.system()
    machine = platform.machine()
    processor = platform.processor() or None
    is_macos = os_name == "Darwin"
    is_apple_silicon = is_macos and machine in ("arm64", "aarch64")
    memory_total_gb = _read_memory_total_gb()
    low_ram = (memory_total_gb is not None and memory_total_gb <= _LOW_RAM_THRESHOLD_GB)
    python_version = platform.python_version()

    gpu_detected, gpu_list = _detect_gpu()

    # Apple Silicon: unified memory = GPU memory
    if is_apple_silicon and not gpu_detected:
        gpu_detected = True
        gpu_list = [GpuInfo(name="Apple Silicon", vram_gb=memory_total_gb, vendor="apple")]

    gpu_name = gpu_list[0].name if gpu_list else None
    vram_gb = gpu_list[0].vram_gb if gpu_list else None

    return HardwareProfile(
        os_name=os_name,
        machine=machine,
        processor=processor,
        is_macos=is_macos,
        is_apple_silicon=is_apple_silicon,
        memory_total_gb=memory_total_gb,
        low_ram=low_ram,
        python_version=python_version,
        gpu_detected=gpu_detected,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        gpus=tuple(gpu_list),
    )


# ---------------------------------------------------------------------------
# Runtime model state
# ---------------------------------------------------------------------------

def _probe_to_dict(probe: ProviderProbe) -> dict:
    return {
        "provider": probe.provider,
        "kind": probe.kind,
        "location": probe.location,
        "status": probe.status,
        "auth_detected": probe.auth_detected,
        "endpoint_detected": probe.endpoint_detected,
        "detected_env_names": probe.detected_env_names,
        "priority": probe.priority,
        "is_primary_candidate": probe.is_primary_candidate,
        "is_fallback_candidate": probe.is_fallback_candidate,
        "detail": probe.detail,
    }


def _build_runtime_model_state(
    api_probes: list[ProviderProbe],
    local_probes: list[ProviderProbe],
) -> RuntimeModelState:
    api_configured = [p for p in api_probes if p.status == "configured"]
    local_available = [p for p in local_probes if p.status == "available"]

    has_api = len(api_configured) > 0
    has_local = len(local_available) > 0

    if has_api and has_local:
        mode: RuntimeModelMode = "hybrid"
    elif has_api:
        mode = "api_only"
    elif has_local:
        mode = "local_only"
    else:
        mode = "unavailable"

    readiness: RuntimeReadiness = "ready" if (has_api or has_local) else "degraded"

    all_candidates = api_configured + local_available
    primary: str | None = None
    fallbacks: list[str] = []
    if all_candidates:
        sorted_candidates = sorted(
            all_candidates, key=lambda p: (p.priority or 999)
        )
        primary = sorted_candidates[0].provider
        fallbacks = [p.provider for p in sorted_candidates[1:]]

    runtime_model_detected = has_api or has_local

    return RuntimeModelState(
        runtime_model_detected=runtime_model_detected,
        mode=mode,
        priority="runtime_defined",
        api_candidates=[_probe_to_dict(p) for p in api_configured],
        local_candidates=[_probe_to_dict(p) for p in local_available],
        primary_candidate=primary,
        fallback_candidates=fallbacks,
        source="probe",
        readiness=readiness,
        note=None,
    )


# ---------------------------------------------------------------------------
# Helm intelligence state
# ---------------------------------------------------------------------------

def _build_helm_intelligence_state(
    api_probes: list[ProviderProbe],
    local_probes: list[ProviderProbe],
    hardware: HardwareProfile,
) -> HelmIntelligenceState:
    # Defaults: calls always disabled by policy
    cloud_calls_enabled = False
    local_model_calls_enabled = False

    has_api = any(p.status == "configured" for p in api_probes)
    has_local = any(p.status == "available" for p in local_probes)

    # Low RAM forces local model calls off
    if hardware.low_ram:
        has_local_effective = False
    else:
        has_local_effective = has_local

    if has_api and has_local_effective:
        mode: HelmIntelligenceMode = "optional_hybrid_available"
        reason = "api and local providers detected; calls remain disabled by policy"
    elif has_api:
        mode = "optional_cloud_available"
        reason = "api provider detected; calls remain disabled by policy"
    elif has_local_effective:
        mode = "optional_local_available"
        reason = "local provider detected; calls remain disabled by policy"
    else:
        mode = "deterministic_only"
        reason = "no providers detected; deterministic mode only"

    return HelmIntelligenceState(
        mode=mode,
        cloud_calls_enabled=cloud_calls_enabled,
        local_model_calls_enabled=local_model_calls_enabled,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover_environment(
    *,
    workspace: Path | None = None,
    timeout_ms: int = 500,
) -> DiscoverySnapshot:
    """Discover runtime, hardware, model state, intelligence state."""
    if workspace is None:
        workspace = Path.cwd()

    runtime = _detect_runtime(workspace)
    hardware = _detect_hardware()

    api_probes = probe_api_providers_from_env()
    local_probes = probe_local_providers(timeout_ms=timeout_ms)

    runtime_model_state = _build_runtime_model_state(api_probes, local_probes)
    helm_intelligence_state = _build_helm_intelligence_state(
        api_probes, local_probes, hardware
    )

    strategy: dict[str, object] = {
        "provider_priority_policy": "runtime_defined",
        "cloud_calls_enabled": helm_intelligence_state.cloud_calls_enabled,
        "local_model_calls_enabled": helm_intelligence_state.local_model_calls_enabled,
        "low_ram": hardware.low_ram,
        "memory_total_gb": hardware.memory_total_gb,
    }

    warnings: list[str] = []
    if runtime_model_state.readiness == "degraded":
        warnings.append("No model providers detected. Helm will run in deterministic-only mode.")
    if hardware.low_ram:
        warnings.append(
            f"Low RAM detected ({hardware.memory_total_gb:.1f} GB). "
            "Local model calls disabled."
        )

    return DiscoverySnapshot(
        runtime=runtime,
        hardware=hardware,
        runtime_model_state=runtime_model_state,
        helm_intelligence_state=helm_intelligence_state,
        strategy=strategy,
        warnings=warnings,
    )


def snapshot_to_json(snapshot: DiscoverySnapshot) -> dict:
    """Convert DiscoverySnapshot to a JSON-serializable dict."""
    return {
        "runtime": {
            "kind": snapshot.runtime.kind,
            "confidence": snapshot.runtime.confidence,
            "adapter": snapshot.runtime.adapter,
            "root": snapshot.runtime.root,
            "markers": snapshot.runtime.markers,
            "reasons": snapshot.runtime.reasons,
        },
        "hardware": {
            "os_name": snapshot.hardware.os_name,
            "machine": snapshot.hardware.machine,
            "processor": snapshot.hardware.processor,
            "is_macos": snapshot.hardware.is_macos,
            "is_apple_silicon": snapshot.hardware.is_apple_silicon,
            "memory_total_gb": snapshot.hardware.memory_total_gb,
            "low_ram": snapshot.hardware.low_ram,
            "python_version": snapshot.hardware.python_version,
            "gpu_detected": snapshot.hardware.gpu_detected,
            "gpu_name": snapshot.hardware.gpu_name,
            "vram_gb": snapshot.hardware.vram_gb,
        },
        "runtime_model_state": {
            "runtime_model_detected": snapshot.runtime_model_state.runtime_model_detected,
            "mode": snapshot.runtime_model_state.mode,
            "priority": snapshot.runtime_model_state.priority,
            "api_candidates": snapshot.runtime_model_state.api_candidates,
            "local_candidates": snapshot.runtime_model_state.local_candidates,
            "primary_candidate": snapshot.runtime_model_state.primary_candidate,
            "fallback_candidates": snapshot.runtime_model_state.fallback_candidates,
            "source": snapshot.runtime_model_state.source,
            "readiness": snapshot.runtime_model_state.readiness,
            "note": snapshot.runtime_model_state.note,
        },
        "helm_intelligence_state": {
            "mode": snapshot.helm_intelligence_state.mode,
            "cloud_calls_enabled": snapshot.helm_intelligence_state.cloud_calls_enabled,
            "local_model_calls_enabled": snapshot.helm_intelligence_state.local_model_calls_enabled,
            "reason": snapshot.helm_intelligence_state.reason,
        },
        "strategy": snapshot.strategy,
        "warnings": snapshot.warnings,
    }
