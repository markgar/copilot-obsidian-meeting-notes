from __future__ import annotations

from pathlib import Path

from .errors import VaultPathError


def resolve_vault_path(value: str | Path) -> Path:
    """Return a canonical existing vault directory."""

    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise VaultPathError(
            "Vault path does not exist.",
            details={"path": str(path)},
        )
    if not path.is_dir():
        raise VaultPathError(
            "Vault path is not a directory.",
            details={"path": str(path)},
        )
    return path


def resolve_vault_target(vault: Path, target: str | Path) -> Path:
    """Resolve a vault-relative target and reject escapes."""

    relative = Path(target)
    if relative.is_absolute():
        raise VaultPathError(
            "Vault target must be relative.",
            details={"target": str(target)},
        )

    root = vault.resolve()
    candidate = root
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise VaultPathError(
                "Vault target may not traverse symbolic links.",
                details={"target": str(target), "component": str(candidate)},
            )

    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise VaultPathError(
            "Vault target resolves outside the vault.",
            details={"target": str(target), "vault": str(root)},
        )
    return resolved
