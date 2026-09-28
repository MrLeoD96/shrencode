"""Serialising runs, so repeated context-menu launches queue instead of competing."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from .console import console


class QueueLock:
    def __init__(self) -> None:
        # The OS releases the lock when this handle closes, including on a crash.
        self._handle = open(Path(tempfile.gettempdir()) / "shrencode.lock", "a+")  # noqa: SIM115

    def _try(self) -> bool:
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def acquire(self) -> None:
        if self._try():
            return
        console.info(console.dim("waiting for another shrencode run to finish"))
        while not self._try():
            time.sleep(5)
