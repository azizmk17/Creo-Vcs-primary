"""Bounded asynchronous J-Link worker. No connection to interactive Creo."""

import ctypes
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from core.services.cad_structure_sync_service import file_hash


_SCAN_LOCK = threading.Lock()
_INTEGRATION = Path(__file__).resolve().parents[2] / "integrations" / "creo_jlink"


def _native_path(path):
    """Creo 3's native launcher has inconsistent support for quoted long paths."""
    buffer = ctypes.create_unicode_buffer(32768)
    get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    get_short.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    size = get_short(str(path), buffer, len(buffer))
    return buffer.value if 0 < size < len(buffer) else str(path)


@contextmanager
def _scan_directory():
    with tempfile.TemporaryDirectory(prefix="nexus-cad-scan-", ignore_cleanup_errors=True) as temp:
        directory = Path(temp)
        try:
            yield directory
        except Exception as exc:
            log_dir = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Nexus" / "creo-scan-logs" / uuid.uuid4().hex
            log_dir.mkdir(parents=True, exist_ok=True)
            for path in directory.rglob("*"):
                if (path.is_file() and "classes" not in path.parts
                        and (path.suffix.casefold() in {".log", ".out", ".err"}
                             or path.name.casefold().startswith(("trail.txt", "std.out", "std.err")))):
                    with path.open("rb") as stream:
                        stream.seek(max(0, path.stat().st_size - 65536))
                        destination = log_dir / path.relative_to(directory)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(stream.read())
            for command, filename in ((["tasklist", "/v", "/fo", "csv"], "processes.csv"),
                                      (["netstat", "-ano"], "netstat.txt")):
                try:
                    result = subprocess.run(command, capture_output=True, timeout=10, check=False)
                    (log_dir / filename).write_bytes(result.stdout[-131072:])
                except Exception:
                    pass
            (log_dir / "failure.txt").write_text(str(exc), encoding="utf-8")
            raise ValueError(str(exc) + "\nScan diagnostics: " + str(log_dir)) from exc
        finally:
            for _ in range(10):
                try:
                    shutil.rmtree(directory)
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    time.sleep(0.2)


class _ProcessJob:
    """Windows kill-on-close job; closing kills only this scan's process tree."""
    def __init__(self):
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                        ("max_ws", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6),
                        ("process_mem", ctypes.c_size_t), ("job_mem", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [("user_time", ctypes.c_int64), ("kernel_time", ctypes.c_int64),
                        ("period_user_time", ctypes.c_int64), ("period_kernel_time", ctypes.c_int64),
                        ("page_faults", wintypes.DWORD), ("total_processes", wintypes.DWORD),
                        ("active_processes", wintypes.DWORD), ("terminated_processes", wintypes.DWORD)]
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       ctypes.c_void_p, wintypes.DWORD,
                                                       ctypes.POINTER(wintypes.DWORD)]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.Accounting = Accounting
        self.handle = self.api.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError("Cannot establish isolated Creo process cleanup.")

    def attach(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            process.kill()
            process.wait()
            raise OSError("Cannot contain the CAD scan process; scan was not started.")

    def close(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                accounting = self.Accounting()
                returned = ctypes.c_uint32()
                if not self.api.QueryInformationJobObject(
                        self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting),
                        ctypes.byref(returned)) or accounting.active_processes == 0:
                    break
                time.sleep(0.05)
            self.api.CloseHandle(self.handle)
            self.handle = None


class CreoStructureWorker:
    def __init__(self):
        common = Path(os.environ.get("NEXUS_CREO_COMMON", r"C:\Program Files\PTC\Creo 3.0\M020\Common Files"))
        jdk = Path(os.environ.get("NEXUS_JAVA_HOME", r"C:\Program Files\Java\jdk1.7.0_80"))
        self.java = jdk / "bin" / "java.exe"
        self.javac = jdk / "bin" / "javac.exe"
        self.jar = common / "text" / "java" / "pfcasync.jar"
        self.common = common
        self.library = common / "x86e_win64" / "lib"
        self.comm = common / "x86e_win64" / "obj" / "pro_comm_msg.exe"
        self.name_service = common / "x86e_win64" / "nms" / "nmsd.exe"
        self.pro_directory = Path(os.environ.get("NEXUS_CREO_PRO_DIRECTORY", str(common)))
        self.launcher = Path(os.environ.get("NEXUS_CREO_START", str(common.parent / "Parametric" / "bin" / "parametric.bat")))
        self.timeout = max(30, min(1800, int(os.environ.get("NEXUS_CREO_SCAN_TIMEOUT", "300"))))

    def signature(self):
        if not self.pro_directory.is_dir():
            raise ValueError("Creo PRO_DIRECTORY is unavailable: " + str(self.pro_directory))
        for path in (self.java, self.javac, self.jar, self.library / "pfcasyncmt.dll",
                     self.comm, self.name_service, self.launcher):
            if not path.is_file():
                raise ValueError("Missing scan runtime: " + str(path) + ". See integrations/creo_jlink/async/README.md.")
        values = {str(p): file_hash(p) for p in (self.jar, self.launcher, Path(__file__),
                  _INTEGRATION / "async" / "NexusStructureScan.java", _INTEGRATION / "src" / "MiniJson.java")}
        values["PRO_DIRECTORY"] = str(self.pro_directory.resolve())
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def run(self, sources, roots, *, cancel=None):
        self.signature()
        if os.name != "nt":
            raise ValueError("The Creo structure worker currently requires Windows.")
        if not _SCAN_LOCK.acquire(blocking=False):
            raise ValueError("Another Creo structure scan is already running. Try again after it finishes.")
        job = None
        process = None
        try:
            # A private HOME and start directory avoid the designer's startup configuration.
            with _scan_directory() as directory:
                classes = directory / "classes"
                classes.mkdir()
                files = {}
                for name, source in sources.items():
                    if cancel is not None and cancel.is_set():
                        raise ValueError("CAD scan cancelled.")
                    source_path = Path(source["path"])
                    target = directory / source_path.name.lower()
                    shutil.copyfile(source_path, target)
                    if file_hash(target) != source["sha256"]:
                        raise ValueError("Source changed while preparing scan: " + name)
                    files[name] = target.name
                (directory / "empty.dat").write_text("", encoding="ascii")
                (directory / "empty-creotk.dat").write_text("", encoding="ascii")
                (directory / "config.pro").write_text(
                    "protkdat " + str(directory / "empty.dat") + "\n"
                    "creotkdat " + str(directory / "empty-creotk.dat") + "\n"
                    "search_path " + str(directory) + "\n", encoding="ascii")
                env = os.environ.copy()
                for key in ("CLASSPATH", "PRO_JAVA_COMMAND", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "PRO_DIRECTORY"):
                    env.pop(key, None)
                env.update(PRO_COMM_MSG_EXE=str(self.comm), PRO_DIRECTORY=str(self.pro_directory),
                           HOME=str(directory))
                # The Java client and its Creo process must use the installed NMS port.
                # PTC's default is 1239; inventing a per-job port breaks corporate firewall rules.
                nms_port = str(os.environ.get("PTCNMSPORT", "1239")).strip()
                if not nms_port.isdigit() or not 1 <= int(nms_port) <= 65535:
                    raise ValueError("PTCNMSPORT must be a valid TCP port number.")
                env["PTCNMSPORT"] = nms_port
                env["PATH"] = str(self.library) + os.pathsep + env.get("PATH", "")
                flags = subprocess.CREATE_NO_WINDOW
                built = subprocess.run([str(self.javac), "-source", "1.7", "-target", "1.7", "-encoding", "UTF-8",
                                        "-classpath", str(self.jar), "-d", str(classes),
                                        str(_INTEGRATION / "src" / "MiniJson.java"),
                                        str(_INTEGRATION / "async" / "NexusStructureScan.java")],
                                       cwd=directory, env=env, capture_output=True, text=True, timeout=60, creationflags=flags)
                if built.returncode:
                    raise ValueError("Async J-Link compilation failed: " + built.stderr[-4000:])
                start_command = '"' + _native_path(self.launcher) + '"'
                if self.launcher.suffix.casefold() != ".bat":
                    psf = self.launcher.with_name("parametric.psf")
                    if not psf.is_file():
                        raise ValueError("Creo startup PSF was not found: " + str(psf))
                    start_command += ' "' + _native_path(psf) + '"'
                start_command += " -g:no_graphics -i:rpc_input"
                request = {"directory": str(directory), "roots": roots, "files": files,
                           "start_command": start_command}
                (directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
                output = directory / "result.json"
                job = _ProcessJob()
                with open(directory / "worker.log", "w", encoding="utf-8") as log:
                    process = subprocess.Popen([str(self.java), "-Djava.library.path=" + str(self.library),
                                                "-classpath", str(classes) + os.pathsep + str(self.jar),
                                                "NexusStructureScan", str(directory / "request.json"), str(output)],
                                               cwd=directory, env=env, stdout=log, stderr=log, creationflags=flags)
                    job.attach(process)
                    deadline = time.monotonic() + self.timeout
                    try:
                        while process.poll() is None:
                            if cancel is not None and cancel.is_set():
                                raise ValueError("CAD scan cancelled.")
                            if time.monotonic() >= deadline:
                                raise ValueError("Creo structure scan timed out. Check license availability and the configured Creo runtime.")
                            time.sleep(0.15)
                    finally:
                        job.close()
                        process.wait(timeout=10)
                if process.returncode or not output.is_file():
                    raise ValueError("Creo worker failed: " + (directory / "worker.log").read_text(encoding="utf-8", errors="replace")[-4000:])
                result = json.loads(output.read_text(encoding="utf-8"))
                result["runtime"] = {"launcher": str(self.launcher), "pfcasync_sha256": file_hash(self.jar)}
                return result
        finally:
            if job:
                job.close()
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            _SCAN_LOCK.release()
