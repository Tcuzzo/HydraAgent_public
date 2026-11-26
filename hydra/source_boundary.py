"""Resolve automatically consumed data inside its selected owner directory."""
from pathlib import Path, PurePath, PureWindowsPath


def _comparison_source(path: PurePath) -> PurePath:
    """Give resolved Windows drive/UNC aliases the same extended spelling."""
    if isinstance(path, PureWindowsPath) and path.is_absolute():
        text = str(path)
        if text.startswith(("\\\\?\\", "\\\\.\\")):
            return path
        prefix = "\\\\?\\UNC\\" + text[2:] if path.drive.startswith("\\\\") else "\\\\?\\" + text
        return PureWindowsPath(prefix)
    return path


def relative_source(path: PurePath, owner: PurePath) -> PurePath:
    """Compare resolved sources without confusing Windows path aliases.

    readlink can return an extended drive/UNC path while resolve returns the
    ordinary spelling for the selected owner. Add the extended prefix to the
    ordinary spelling; do not strip prefixes or reinterpret device paths.
    """
    return _comparison_source(path).relative_to(_comparison_source(owner))


def confined_source(path: Path, owner: Path) -> Path:
    """Allow internal aliases without following a source outside its owner."""
    try:
        resolved = path.expanduser().resolve()
        boundary = owner.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("source path cannot be resolved inside its selected root") from exc
    try:
        relative_source(resolved, boundary)
    except ValueError:
        raise ValueError("source path escapes its selected root") from None
    return resolved
