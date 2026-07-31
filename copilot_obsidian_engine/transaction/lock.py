from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from ..errors import LockError

LOCK_FILE = ".copilot-obsidian-lock"


class VaultLock:
    def __init__(self, vault: Path) -> None:
        self.path = vault / LOCK_FILE
        self.token = uuid.uuid4().hex
        self._acquired = False

    def __enter__(self) -> "VaultLock":
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "token": self.token,
                "created_unix": time.time(),
            },
            sort_keys=True,
        ).encode("utf-8")
        descriptor = self._create_lock()

        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        except OSError:
            self.path.unlink(missing_ok=True)
            raise
        finally:
            os.close(descriptor)
        self._acquired = True
        return self

    def _create_lock(self) -> int:
        for attempt in range(2):
            try:
                return os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError as error:
                owner = self._read_owner()
                if attempt == 0 and self._remove_stale_lock(owner):
                    continue
                raise LockError(
                    "Vault is locked by another transaction.",
                    details={"path": str(self.path), "owner": owner},
                ) from error
        raise LockError("Vault lock could not be acquired.")

    def _read_owner(self) -> object:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"state": "unreadable"}

    def _remove_stale_lock(self, owner: object) -> bool:
        if not isinstance(owner, dict) or not isinstance(owner.get("pid"), int):
            return False
        pid = owner["pid"]
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        except PermissionError:
            return False
        except OSError:
            return False
        else:
            return False

        current = self._read_owner()
        if current != owner:
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        return True

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            owner = self._read_owner()
            if not isinstance(owner, dict) or owner.get("token") != self.token:
                raise LockError(
                    "Vault lock ownership changed before release.",
                    details={"path": str(self.path)},
                )
            self.path.unlink()
        finally:
            self._acquired = False

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()
