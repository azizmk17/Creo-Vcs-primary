"""Opt-in, bounded and anonymous measurements for real application sessions.

Only fixed operation names, timing values and counters leave the process. SQL,
parameters, paths, user names and engineering data are never put in the report.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import json
import math
import os
import platform
import sqlite3
import sys
import threading
import time
import uuid
import zipfile


_BUCKETS_MS = (1, 5, 10, 20, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000)
_MAX_EVENTS = 3000
_MAX_RESOURCE_SAMPLES = 1800
_NETWORK_OPTIONS = {"company_network", "vpn", "other", "unknown"}
_RUN_OPTIONS = {"first_session", "repeat_session", "normal_work", "unknown"}
_SQL_VERBS = {"SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE", "WITH", "PRAGMA",
              "CREATE", "ALTER", "DROP", "BEGIN", "COMMIT", "ROLLBACK"}
_UNSET = object()


class PerformanceRecorder:
    def __init__(self):
        self._lock = threading.RLock()
        self._active = False
        self._generation = 0
        self._metrics = {}
        self._events = []
        self._dropped_events = 0
        self._resources = []
        self._dropped_resource_samples = 0

    @property
    def active(self):
        return self._active

    def start(self, network="unknown", run_type="unknown"):
        with self._lock:
            if self._active:
                return False
            self._generation += 1
            self._active = True
            self._origin = time.perf_counter()
            self._started_utc = datetime.now(timezone.utc).isoformat()
            self._run_id = uuid.uuid4().hex
            self._network = network if network in _NETWORK_OPTIONS else "unknown"
            self._run_type = run_type if run_type in _RUN_OPTIONS else "unknown"
            self._metrics = {}
            self._events = []
            self._dropped_events = 0
            self._resources = []
            self._dropped_resource_samples = 0
            return True

    def token(self):
        if not self._active:
            return None
        return self._generation

    def begin(self, name):
        token = self.token()
        return (token, time.perf_counter(), name) if token is not None else None

    def end(self, span, outcome="ok", *, event=True):
        if span is None:
            return
        token, started, name = span
        self.observe(name, time.perf_counter() - started, token, outcome, event=event)

    def observe(self, name, seconds, token, outcome="ok", *, event=False):
        if token is None or not self._active:
            return
        milliseconds = max(0.0, float(seconds) * 1000.0)
        if not math.isfinite(milliseconds):
            return
        with self._lock:
            if not self._active or token != self._generation:
                return
            metric = self._metrics.setdefault(name, {
                "count": 0, "total_ms": 0.0, "min_ms": None, "max_ms": 0.0,
                "failed": 0, "cancelled": 0, "histogram": [0] * (len(_BUCKETS_MS) + 1),
            })
            metric["count"] += 1
            metric["total_ms"] += milliseconds
            metric["min_ms"] = milliseconds if metric["min_ms"] is None else min(metric["min_ms"], milliseconds)
            metric["max_ms"] = max(metric["max_ms"], milliseconds)
            if outcome == "error":
                metric["failed"] += 1
            elif outcome == "cancelled":
                metric["cancelled"] += 1
            index = next((i for i, limit in enumerate(_BUCKETS_MS) if milliseconds <= limit), len(_BUCKETS_MS))
            metric["histogram"][index] += 1
            if event:
                if len(self._events) < _MAX_EVENTS:
                    self._events.append({
                        "operation": name,
                        "ended_after_start_ms": round((time.perf_counter() - self._origin) * 1000, 3),
                        "duration_ms": round(milliseconds, 3),
                        "outcome": outcome if outcome in {"ok", "error", "cancelled"} else "ok",
                    })
                else:
                    self._dropped_events += 1

    def snapshot(self, *, version=None, startup=None):
        with self._lock:
            if not self._active:
                raise ValueError("Performance recording is not active.")
            metrics = {}
            for name, item in self._metrics.items():
                metrics[name] = {
                    "count": item["count"],
                    "total_ms": round(item["total_ms"], 3),
                    "mean_ms": round(item["total_ms"] / item["count"], 3),
                    "min_ms": round(item["min_ms"], 3),
                    "max_ms": round(item["max_ms"], 3),
                    "failed": item["failed"],
                    "cancelled": item["cancelled"],
                    "histogram_counts": list(item["histogram"]),
                }
            return {
                "format_version": 1,
                "run_id": self._run_id,
                "started_utc": self._started_utc,
                "duration_seconds": round(time.perf_counter() - self._origin, 3),
                "app_version": str(version or "unknown"),
                "frozen": bool(getattr(sys, "frozen", False)),
                "python_version": platform.python_version(),
                "platform": {
                    "system": platform.system(), "release": platform.release(),
                    "architecture": platform.machine(), "logical_cpus": os.cpu_count(),
                },
                "network_selected_by_user": self._network,
                "run_type_selected_by_user": self._run_type,
                "startup_milestones": list(startup or []),
                "histogram_upper_bounds_ms": list(_BUCKETS_MS) + [None],
                "metrics": metrics,
                "events": list(self._events),
                "dropped_events": self._dropped_events,
                "resource_samples": list(self._resources),
                "dropped_resource_samples": self._dropped_resource_samples,
                "limitations": [
                    "Startup milestones begin when the Python entry point starts, not at operating-system process launch; no pre-recording database calls are measured.",
                    "SQLite measurements cover instrumented connections opened while recording, not existing connections or connections that bypass the application wrapper.",
                    "SQL execution, fetch, connection and transaction timings are elapsed wall time; they include CPU, filesystem, network and lock waiting together.",
                    "Cursor iteration and implicit SQLite work are not fully measured. Metrics can overlap and must not be summed as independent elapsed time.",
                    "UI delay is timer lateness, which may include operating-system scheduling, sleep, or backgrounding; it is not proof of an application freeze.",
                    "Resource samples cover this Nexus process only. CPU percent is relative to one logical CPU and can exceed 100%; working set is sampled Windows physical memory, not a continuous peak.",
                    "Timing instrumentation adds some overhead. A missing metric means it was not observed, not that an operation took zero time.",
                ],
            }

    def record_resources(self, token, cpu_percent, working_set_bytes):
        if token is None or not self._active:
            return
        with self._lock:
            if not self._active or token != self._generation:
                return
            if len(self._resources) >= _MAX_RESOURCE_SAMPLES:
                self._dropped_resource_samples += 1
                return
            cpu = float(cpu_percent) if cpu_percent is not None else None
            if cpu is not None and (not math.isfinite(cpu) or cpu < 0):
                cpu = None
            memory = int(working_set_bytes) if working_set_bytes is not None else None
            if memory is not None and memory < 0:
                memory = None
            self._resources.append({
                "after_start_ms": round((time.perf_counter() - self._origin) * 1000, 3),
                "process_cpu_percent": round(cpu, 3) if cpu is not None else None,
                "working_set_bytes": memory,
            })

    def stop(self):
        with self._lock:
            self._active = False
            self._generation += 1


recorder = PerformanceRecorder()


def timed(name, *, event=True):
    """Time one named operation without collecting its arguments or results."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            span = recorder.begin(name)
            if span is None:
                return function(*args, **kwargs)
            try:
                result = function(*args, **kwargs)
            except BaseException:
                recorder.end(span, "error", event=event)
                raise
            recorder.end(span, event=event)
            return result
        return wrapped
    return decorate


@contextmanager
def measured(name, *, event=True):
    span = recorder.begin(name)
    try:
        yield
    except BaseException:
        recorder.end(span, "error", event=event)
        raise
    else:
        recorder.end(span, event=event)


def _sql_kind(sql):
    if not isinstance(sql, str):
        return "OTHER"
    first = sql.lstrip().split(None, 1)
    verb = first[0].upper() if first else "OTHER"
    return verb if verb in _SQL_VERBS else "OTHER"


class RecordingCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        with measured("sqlite.execute." + _sql_kind(sql), event=False):
            return super().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        with measured("sqlite.executemany." + _sql_kind(sql), event=False):
            return super().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        with measured("sqlite.executescript", event=False):
            return super().executescript(sql_script)

    def fetchone(self):
        with measured("sqlite.fetch.one", event=False):
            return super().fetchone()

    def fetchmany(self, size=_UNSET):
        with measured("sqlite.fetch.many", event=False):
            return super().fetchmany() if size is _UNSET else super().fetchmany(size)

    def fetchall(self):
        with measured("sqlite.fetch.all", event=False):
            return super().fetchall()


class RecordingConnection(sqlite3.Connection):
    def cursor(self, *args, **kwargs):
        if not args and "factory" not in kwargs:
            kwargs["factory"] = RecordingCursor
        return super().cursor(*args, **kwargs)

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return self.cursor().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        return self.cursor().executescript(sql_script)

    def commit(self):
        with measured("sqlite.commit", event=False):
            return super().commit()

    def rollback(self):
        with measured("sqlite.rollback", event=False):
            return super().rollback()

    def __exit__(self, exc_type, exc_value, traceback):
        with measured("sqlite.transaction_context", event=False):
            return super().__exit__(exc_type, exc_value, traceback)


def write_report(path, report):
    """Write a shareable ZIP. The caller chooses the local destination."""
    guide = (
        "Nexus performance recording\n\n"
        "Open performance.json to inspect the anonymous measurements. "
        "Send this ZIP to the person analyzing your performance.\n"
        "The report includes no SQL statements, SQL parameters, file paths, "
        "user names, item names, database contents or engineering files.\n"
        "See the limitations array in performance.json before comparing timings.\n"
    )
    summary = [
        "Nexus performance recording",
        f"Application version: {report.get('app_version', 'unknown')}",
        f"Duration: {report.get('duration_seconds', 0)} seconds",
        f"Connection selected: {report.get('network_selected_by_user', 'unknown')}",
        f"Session selected: {report.get('run_type_selected_by_user', 'unknown')}",
        "",
        "Operation | count | mean milliseconds | maximum milliseconds | errors | cancelled",
    ]
    for name, metric in sorted((report.get("metrics") or {}).items()):
        summary.append(
            f"{name} | {metric['count']} | {metric['mean_ms']} | {metric['max_ms']} | "
            f"{metric['failed']} | {metric['cancelled']}"
        )
    samples = report.get("resource_samples") or []
    memory = [row["working_set_bytes"] for row in samples if row.get("working_set_bytes") is not None]
    cpu = [row["process_cpu_percent"] for row in samples if row.get("process_cpu_percent") is not None]
    summary += ["", f"Resource samples: {len(samples)}"]
    if memory:
        summary.append(f"Highest sampled working set: {max(memory) / 1024 ** 2:.1f} MiB")
    if cpu:
        summary.append(f"Highest sampled process CPU: {max(cpu):.1f}% of one logical CPU")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", guide)
        archive.writestr("summary.txt", "\n".join(summary) + "\n")
        archive.writestr("performance.json", json.dumps(report, ensure_ascii=False, indent=2))
