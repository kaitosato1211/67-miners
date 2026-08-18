"""Runtime resource-usage sampling for validator status snapshots."""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

_CGROUP_V2_MEMORY_MAX_PATH = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V1_MEMORY_LIMIT_PATH = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
_CGROUP_V2_CPU_MAX_PATH = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_CPU_QUOTA_PATH = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_CPU_PERIOD_PATH = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
_PROC_SELF_CGROUP_PATH = Path("/proc/self/cgroup")
_PROC_SELF_MOUNTINFO_PATH = Path("/proc/self/mountinfo")


@dataclass(frozen=True, slots=True)
class ValidatorResourceUsageSnapshot:
    captured_at: datetime
    cpu_percent: float
    cpu_capacity_cores: float
    memory_used_bytes: int
    memory_total_bytes: int
    memory_percent: float
    disk_used_bytes: int
    disk_total_bytes: int
    disk_percent: float


@dataclass(slots=True)
class ValidatorResourceUsageProvider:
    disk_usage_path: Path = field(default_factory=Path.cwd)
    _previous_cpu_seconds: float = field(init=False, repr=False)
    _previous_wall_seconds: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.disk_usage_path = self.disk_usage_path.expanduser()
        cpu_seconds, wall_seconds = _read_cpu_snapshot()
        self._previous_cpu_seconds = cpu_seconds
        self._previous_wall_seconds = wall_seconds

    def snapshot(self) -> ValidatorResourceUsageSnapshot:
        cpu_seconds, wall_seconds = _read_cpu_snapshot()
        cpu_percent = _cpu_percent(
            current_cpu_seconds=cpu_seconds,
            current_wall_seconds=wall_seconds,
            previous_cpu_seconds=self._previous_cpu_seconds,
            previous_wall_seconds=self._previous_wall_seconds,
        )
        self._previous_cpu_seconds = cpu_seconds
        self._previous_wall_seconds = wall_seconds

        memory_used_bytes = _read_process_rss_bytes()
        memory_total_bytes = _read_total_memory_bytes()
        disk_total_bytes, disk_used_bytes, disk_percent = _read_disk_usage(self.disk_usage_path)
        return ValidatorResourceUsageSnapshot(
            captured_at=datetime.now(UTC),
            cpu_percent=cpu_percent,
            cpu_capacity_cores=_read_cpu_capacity_cores(),
            memory_used_bytes=memory_used_bytes,
            memory_total_bytes=memory_total_bytes,
            memory_percent=_usage_percent(memory_used_bytes, memory_total_bytes),
            disk_used_bytes=disk_used_bytes,
            disk_total_bytes=disk_total_bytes,
            disk_percent=disk_percent,
        )


def _read_cpu_snapshot() -> tuple[float, float]:
    return time.process_time(), time.monotonic()


def _cpu_percent(
    *,
    current_cpu_seconds: float,
    current_wall_seconds: float,
    previous_cpu_seconds: float,
    previous_wall_seconds: float,
) -> float:
    elapsed_wall_seconds = current_wall_seconds - previous_wall_seconds
    if elapsed_wall_seconds <= 0:
        return 0.0
    elapsed_cpu_seconds = current_cpu_seconds - previous_cpu_seconds
    if elapsed_cpu_seconds <= 0:
        return 0.0
    return (elapsed_cpu_seconds / elapsed_wall_seconds) * 100.0


def _read_cpu_capacity_cores() -> float:
    quota_cores = _read_cgroup_cpu_quota_cores()
    affinity_cores = _read_process_affinity_cpu_count()
    if quota_cores is not None and affinity_cores is not None:
        return min(quota_cores, affinity_cores)
    if quota_cores is not None:
        return quota_cores
    if affinity_cores is not None:
        return affinity_cores
    return max(float(os.cpu_count() or 1), 1.0)


def _read_process_affinity_cpu_count() -> float | None:
    try:
        affinity = os.sched_getaffinity(0)
    except (AttributeError, OSError):
        return None
    count = len(affinity)
    if count <= 0:
        return None
    return float(count)


def _read_cgroup_cpu_quota_cores() -> float | None:
    finite_quotas: list[float] = []
    for cpu_max_path in _candidate_cgroup_v2_cpu_max_paths():
        quota = _read_cgroup_v2_cpu_quota_cores(cpu_max_path)
        if quota is not None:
            finite_quotas.append(quota)
    for quota_path, period_path in _candidate_cgroup_v1_cpu_quota_paths():
        quota = _read_cgroup_v1_cpu_quota_cores(quota_path, period_path)
        if quota is not None:
            finite_quotas.append(quota)
    if not finite_quotas:
        return None
    return min(finite_quotas)


def _read_cgroup_v2_cpu_quota_cores(cpu_max_path: Path) -> float | None:
    try:
        raw_value = cpu_max_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    parts = raw_value.split()
    if len(parts) != 2:
        return None
    quota_raw, period_raw = parts
    if quota_raw == "max":
        return None
    try:
        quota = int(quota_raw)
        period = int(period_raw)
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return quota / period


def _read_cgroup_v1_cpu_quota_cores(quota_path: Path, period_path: Path) -> float | None:
    try:
        quota = int(quota_path.read_text(encoding="utf-8").strip())
        period = int(period_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if quota <= 0 or period <= 0:
        return None
    return quota / period


def _candidate_cgroup_v2_cpu_max_paths() -> tuple[Path, ...]:
    paths: list[Path] = []
    memberships = _read_cgroup_memberships()
    mounts = _read_cgroup_mounts()
    for membership in memberships:
        hierarchy_id, controllers, cgroup_path = membership
        if hierarchy_id != "0" or controllers:
            continue
        for mount in mounts:
            fstype, mount_point, mount_root, _mount_controllers = mount
            if fstype != "cgroup2":
                continue
            for path in _cgroup_path_candidates(mount_point, mount_root, cgroup_path):
                paths.append(path / "cpu.max")
    paths.append(_CGROUP_V2_CPU_MAX_PATH)
    return _dedupe_paths(paths)


def _candidate_cgroup_v1_cpu_quota_paths() -> tuple[tuple[Path, Path], ...]:
    pairs: list[tuple[Path, Path]] = []
    memberships = _read_cgroup_memberships()
    mounts = _read_cgroup_mounts()
    for membership in memberships:
        _hierarchy_id, controllers, cgroup_path = membership
        if "cpu" not in controllers:
            continue
        for mount in mounts:
            fstype, mount_point, mount_root, mount_controllers = mount
            if fstype != "cgroup":
                continue
            if "cpu" not in mount_controllers:
                continue
            for path in _cgroup_path_candidates(mount_point, mount_root, cgroup_path):
                pairs.append((path / "cpu.cfs_quota_us", path / "cpu.cfs_period_us"))
    pairs.append((_CGROUP_V1_CPU_QUOTA_PATH, _CGROUP_V1_CPU_PERIOD_PATH))
    return _dedupe_path_pairs(pairs)


def _read_cgroup_memberships() -> tuple[tuple[str, frozenset[str], PurePosixPath], ...]:
    try:
        lines = _PROC_SELF_CGROUP_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    memberships: list[tuple[str, frozenset[str], PurePosixPath]] = []
    for line in lines:
        hierarchy_id, separator, rest = line.partition(":")
        if not separator:
            continue
        controllers_raw, separator, path_raw = rest.partition(":")
        if not separator:
            continue
        controllers = frozenset(segment for segment in controllers_raw.split(",") if segment)
        memberships.append((hierarchy_id, controllers, PurePosixPath(path_raw or "/")))
    return tuple(memberships)


def _read_cgroup_mounts() -> tuple[tuple[str, Path, PurePosixPath, frozenset[str]], ...]:
    try:
        lines = _PROC_SELF_MOUNTINFO_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    mounts: list[tuple[str, Path, PurePosixPath, frozenset[str]]] = []
    for line in lines:
        parts = line.split()
        try:
            separator_index = parts.index("-")
        except ValueError:
            continue
        if separator_index + 3 >= len(parts) or separator_index < 5:
            continue
        mount_root = PurePosixPath(_unescape_mountinfo_path(parts[3]))
        mount_point = Path(_unescape_mountinfo_path(parts[4]))
        fstype = parts[separator_index + 1]
        super_options = parts[separator_index + 3]
        controllers = frozenset(segment for segment in super_options.split(",") if segment)
        mounts.append((fstype, mount_point, mount_root, controllers))
    return tuple(mounts)


def _cgroup_path_candidates(
    mount_point: Path,
    mount_root: PurePosixPath,
    cgroup_path: PurePosixPath,
) -> tuple[Path, ...]:
    relative_cgroup = _relative_cgroup_path(cgroup_path, mount_root)
    parts = [part for part in relative_cgroup.parts if part not in ("", "/")]
    paths: list[Path] = []
    for end_index in range(len(parts), -1, -1):
        paths.append(mount_point.joinpath(*parts[:end_index]))
    return tuple(paths)


def _relative_cgroup_path(cgroup_path: PurePosixPath, mount_root: PurePosixPath) -> PurePosixPath:
    try:
        return cgroup_path.relative_to(mount_root)
    except ValueError:
        try:
            return cgroup_path.relative_to("/")
        except ValueError:
            return cgroup_path


def _unescape_mountinfo_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _dedupe_paths(paths: list[Path]) -> tuple[Path, ...]:
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return tuple(deduped)


def _dedupe_path_pairs(pairs: list[tuple[Path, Path]]) -> tuple[tuple[Path, Path], ...]:
    deduped: list[tuple[Path, Path]] = []
    seen: set[tuple[Path, Path]] = set()
    for pair in pairs:
        if pair in seen:
            continue
        seen.add(pair)
        deduped.append(pair)
    return tuple(deduped)


def _read_process_rss_bytes() -> int:
    statm = Path("/proc/self/statm").read_text(encoding="utf-8").strip().split()
    if len(statm) < 2:
        raise RuntimeError("unexpected /proc/self/statm format")
    resident_pages = int(statm[1])
    return resident_pages * os.sysconf("SC_PAGE_SIZE")


def _read_total_memory_bytes() -> int:
    host_total_memory_bytes = _read_host_total_memory_bytes()
    for path in (_CGROUP_V2_MEMORY_MAX_PATH, _CGROUP_V1_MEMORY_LIMIT_PATH):
        limit = _read_finite_memory_limit(path, host_total_memory_bytes)
        if limit is not None:
            return limit
    return host_total_memory_bytes


def _read_finite_memory_limit(path: Path, host_total_memory_bytes: int) -> int | None:
    if not path.exists():
        return None
    raw_value = path.read_text(encoding="utf-8").strip()
    if raw_value == "max":
        return None
    limit = int(raw_value)
    if limit <= 0:
        return None
    if limit >= host_total_memory_bytes:
        return None
    return limit


def _read_host_total_memory_bytes() -> int:
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def _read_disk_usage(path: Path) -> tuple[int, int, float]:
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, _usage_percent(usage.used, usage.total)


def _usage_percent(used_bytes: int, total_bytes: int) -> float:
    if total_bytes <= 0:
        return 0.0
    return (used_bytes / total_bytes) * 100.0


__all__ = ["ValidatorResourceUsageProvider", "ValidatorResourceUsageSnapshot"]
