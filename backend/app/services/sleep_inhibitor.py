"""Run davomida kompyuterning uyqu rejimiga ketishini bloklaydi.

Windows'da SetThreadExecutionState, macOS'da caffeinate, Linux'da
systemd-inhibit ishlatiladi. Hech biri mavjud bo'lmasa, run baribir
davom etadi — bu yordamchi xizmat, majburiy shart emas.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from types import TracebackType

from app.core.config import settings


class SleepInhibitor:
    def __init__(self, reason: str = "VideoWorkflow batch") -> None:
        self.reason = reason
        self.active = False
        self._process: subprocess.Popen | None = None
        self._windows = False

    def start(self) -> None:
        if not settings.PREVENT_SLEEP or self.active:
            return
        try:
            if os.name == "nt":
                self._start_windows()
            elif sys.platform == "darwin":
                self._start_process(["caffeinate", "-dimsu"])
            else:
                self._start_linux()
        except Exception as exc:
            print(f"[SLEEP] inhibitor ishga tushmadi: {exc}", flush=True)

    def _start_windows(self) -> None:
        es_continuous = 0x80000000
        es_system_required = 0x00000001
        result = ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
            es_continuous | es_system_required
        )
        if not result:
            raise RuntimeError("SetThreadExecutionState failed")
        self._windows = True
        self.active = True

    def _start_linux(self) -> None:
        if shutil.which("systemd-inhibit"):
            self._start_process(
                [
                    "systemd-inhibit",
                    "--what=sleep:idle",
                    "--who=VideoWorkflow",
                    f"--why={self.reason}",
                    "--mode=block",
                    "sleep",
                    "infinity",
                ]
            )
            return
        if shutil.which("caffeinate"):
            self._start_process(["caffeinate", "-i"])
            return
        print("[SLEEP] systemd-inhibit/caffeinate topilmadi", flush=True)

    def _start_process(self, command: list[str]) -> None:
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        self.active = True

    def stop(self) -> None:
        if not self.active:
            return
        try:
            if self._windows:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # type: ignore[attr-defined]
            if self._process is not None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except Exception:
                    self._process.kill()
        except Exception as exc:
            print(f"[SLEEP] inhibitor to'xtatilmadi: {exc}", flush=True)
        finally:
            self._process = None
            self._windows = False
            self.active = False

    def __enter__(self) -> "SleepInhibitor":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()


def prevent_sleep(reason: str = "VideoWorkflow batch") -> SleepInhibitor:
    return SleepInhibitor(reason)
