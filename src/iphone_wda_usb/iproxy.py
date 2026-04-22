"""Manage a USB port-forward to WebDriverAgent using iproxy."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional


class IProxyError(RuntimeError):
    """Raised when iproxy cannot be started."""


@dataclass
class IProxy:
    """Context manager for `iproxy <local> <remote> [udid]`."""

    local_port: int = 8100
    remote_port: int = 8100
    udid: Optional[str] = None
    executable: str = "iproxy"

    def __post_init__(self) -> None:
        self.process: Optional[subprocess.Popen[bytes]] = None

    def start(self) -> "IProxy":
        if self.process and self.process.poll() is None:
            return self
        if not shutil.which(self.executable):
            raise IProxyError(
                f"{self.executable!r} was not found. Install libusbmuxd/libimobiledevice or npm's iproxy first."
            )

        cmd = self._modern_command()
        self.process = self._spawn(cmd)
        time.sleep(0.4)
        if self.process.poll() is not None:
            stdout, stderr = self.process.communicate(timeout=1)
            first_output = (stderr or stdout).decode("utf-8", errors="replace").strip()
            legacy_cmd = self._legacy_command()
            if legacy_cmd != cmd:
                self.process = self._spawn(legacy_cmd)
                time.sleep(0.4)
                if self.process.poll() is None:
                    return self
                stdout, stderr = self.process.communicate(timeout=1)
                legacy_output = (stderr or stdout).decode("utf-8", errors="replace").strip()
                raise IProxyError(
                    "iproxy exited immediately. "
                    f"Modern command output: {first_output}. Legacy command output: {legacy_output}"
                )
            raise IProxyError(f"iproxy exited immediately: {first_output}")
        return self

    def _spawn(self, cmd: list[str]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _modern_command(self) -> list[str]:
        cmd = [self.executable]
        if self.udid:
            cmd.extend(["-u", self.udid])
        cmd.append(f"{self.local_port}:{self.remote_port}")
        return cmd

    def _legacy_command(self) -> list[str]:
        cmd = [self.executable, str(self.local_port), str(self.remote_port)]
        if self.udid:
            cmd.append(self.udid)
        return cmd

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)

    def __enter__(self) -> "IProxy":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.stop()
