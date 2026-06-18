from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


def _new_log_buffer() -> deque[str]:
    return deque(maxlen=5000)


@dataclass
class JobRun:
    run_id: str
    job_key: str
    cmd: list[str]
    started_at: float
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    lines: deque[str] = field(default_factory=_new_log_buffer)
    done: threading.Event = field(default_factory=threading.Event)
    proc: Optional[subprocess.Popen[str]] = None
    log_path: Optional[str] = None

    @property
    def status(self) -> str:
        if not self.done.is_set():
            return "running"
        if self.exit_code == 0:
            return "success"
        return "failed"

    def to_public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "job_key": self.job_key,
            "cmd": self.cmd,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_code": self.exit_code,
            "status": self.status,
            "duration": round((self.ended_at or time.time()) - self.started_at, 1),
            "line_count": len(self.lines),
        }


class Runner:
    def __init__(
        self,
        logs_dir: Path,
        project_root: Path,
        jobs_map: dict[str, Any],
        state_file: Path,
        max_keep: int = 50,
    ):
        self.logs_dir = logs_dir
        self.project_root = project_root
        self.jobs_map = jobs_map
        self.state_file = state_file
        self.runs: dict[str, JobRun] = {}
        self._lock = threading.Lock()
        self._max = max_keep
        self._load_state()

    def _tail_log_lines(self, log_path: Path, max_lines: int = 3000) -> list[str]:
        if not log_path.is_file():
            return []
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                return [line.rstrip("\n") for line in deque(f, maxlen=max_lines)]
        except Exception:
            return []

    def _run_to_state(self, run: JobRun) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "job_key": run.job_key,
            "cmd": run.cmd,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "exit_code": run.exit_code,
            "log_path": run.log_path,
        }

    def _save_state_locked(self) -> None:
        payload = {
            "version": 1,
            "saved_at": time.time(),
            "runs": [self._run_to_state(r) for r in self.list_runs()],
        }
        tmp = self.state_file.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            tmp.replace(self.state_file)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def _save_state(self) -> None:
        with self._lock:
            self._save_state_locked()

    def _load_state(self) -> None:
        if not self.state_file.is_file():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return
        now = time.time()
        restored: dict[str, JobRun] = {}
        for item in data.get("runs", []):
            try:
                run = JobRun(
                    run_id=str(item["run_id"]),
                    job_key=str(item["job_key"]),
                    cmd=list(item.get("cmd") or []),
                    started_at=float(item.get("started_at") or now),
                    ended_at=(float(item["ended_at"]) if item.get("ended_at") is not None else None),
                    exit_code=(int(item["exit_code"]) if item.get("exit_code") is not None else None),
                    log_path=item.get("log_path"),
                )
            except Exception:
                continue

            if run.log_path:
                for line in self._tail_log_lines(Path(run.log_path), max_lines=5000):
                    run.lines.append(line)

            if run.ended_at is None:
                run.ended_at = now
                run.exit_code = -2
                run.lines.append("⚠ Run interrompu par un redémarrage du service webui.")
            run.done.set()
            restored[run.run_id] = run

        self.runs = {
            r.run_id: r
            for r in sorted(restored.values(), key=lambda x: x.started_at, reverse=True)[: self._max]
        }

    def launch(
        self,
        job_key: str,
        extra_args: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> JobRun:
        jd = self.jobs_map.get(job_key)
        if not jd:
            raise KeyError(f"job inconnu: {job_key}")
        cmd = list(jd.cmd) + list(extra_args or [])
        return self._run_cmd(job_key, cmd, extra_env=extra_env)

    def launch_custom(
        self,
        job_key: str,
        cmd: list[str],
        extra_env: dict[str, str] | None = None,
    ) -> JobRun:
        return self._run_cmd(job_key, cmd, extra_env=extra_env)

    def _run_cmd(
        self,
        job_key: str,
        cmd: list[str],
        extra_env: dict[str, str] | None = None,
    ) -> JobRun:
        run_id = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
        run = JobRun(run_id=run_id, job_key=job_key, cmd=cmd, started_at=time.time())
        with self._lock:
            self.runs[run_id] = run
            if len(self.runs) > self._max:
                old = sorted(self.runs.values(), key=lambda r: r.started_at)[: -self._max]
                for r in old:
                    if r.done.is_set():
                        self.runs.pop(r.run_id, None)
            self._save_state_locked()

        log_path = self.logs_dir / f"{run_id}-{job_key}.log"
        run.log_path = str(log_path)
        self._save_state()

        def target():
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            if extra_env:
                for key, value in extra_env.items():
                    if key and value is not None:
                        env[str(key)] = str(value)
            try:
                run.proc = subprocess.Popen(
                    cmd,
                    cwd=self.project_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                    text=True,
                    env=env,
                )
            except Exception as exc:
                run.lines.append(f"❌ Échec du lancement : {exc}")
                run.exit_code = -1
                run.ended_at = time.time()
                run.done.set()
                return

            with log_path.open("w", encoding="utf-8") as flog:
                assert run.proc.stdout
                _ansi_re = __import__("re").compile(r"\x1b\[[0-9;]*[mKHJABCDsu]")
                for line in run.proc.stdout:
                    line = _ansi_re.sub("", line.rstrip("\n"))
                    run.lines.append(line)
                    flog.write(line + "\n")
                    flog.flush()
            run.proc.wait()
            run.exit_code = run.proc.returncode
            run.ended_at = time.time()
            run.done.set()
            self._save_state()

        threading.Thread(target=target, daemon=True, name=f"job-{run_id}").start()
        return run

    def get(self, run_id: str) -> Optional[JobRun]:
        return self.runs.get(run_id)

    def list_runs(self) -> list[JobRun]:
        return sorted(self.runs.values(), key=lambda r: r.started_at, reverse=True)

    def running_runs(self) -> list[JobRun]:
        return [r for r in self.runs.values() if not r.done.is_set()]

    def kill(self, run_id: str) -> bool:
        r = self.get(run_id)
        if r and r.proc and not r.done.is_set():
            r.proc.terminate()
            return True
        return False

    def cleanup_running(self) -> None:
        for r in self.runs.values():
            if r.proc and not r.done.is_set():
                try:
                    r.proc.terminate()
                except Exception:
                    pass
