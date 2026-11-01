from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class UnsafePathError(ValueError):
    pass


def normalize(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def filesystem_path(path: Path | str) -> str:
    """Internal extended-length path; user-facing/manifest paths stay canonical."""
    text = os.path.abspath(str(path))
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text
    return text


def lstat(path: Path | str) -> os.stat_result:
    return os.stat(filesystem_path(path), follow_symlinks=False)


def is_within(path: Path | str, parent: Path | str, *, include_root: bool = True) -> bool:
    child, root = normalize(path), normalize(parent)
    try:
        inside = os.path.commonpath((child, root)) == root
    except ValueError:
        return False
    return inside and (include_root or child != root)


def is_reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )


def validate_ancestry(path: Path) -> None:
    for ancestor in reversed((path, *path.parents)):
        metadata = lstat(ancestor)
        if is_reparse(metadata):
            raise UnsafePathError(f"Reparse points are never followed: {ancestor}")


@dataclass(frozen=True, slots=True)
class Locations:
    profile: Path
    local: Path
    roaming: Path
    program_data: Path
    windows: Path
    temp: Path
    state: Path
    user_folders: tuple[Path, ...] = ()
    documents: Path | None = None

    @classmethod
    def discover(cls) -> Locations:
        from tatclean.native.system import known_folder, temporary_directory, windows_directory

        profile = known_folder("profile")
        local = known_folder("local")
        folders = []
        for key, fallback in (
            ("desktop", "Desktop"), ("documents", "Documents"), ("downloads", "Downloads"),
            ("pictures", "Pictures"), ("music", "Music"), ("videos", "Videos"),
        ):
            try:
                folders.append(known_folder(key))
            except OSError:
                folders.append(profile / fallback)
        return cls(
            profile=profile,
            local=local,
            roaming=known_folder("roaming"),
            program_data=known_folder("program_data"),
            windows=windows_directory(),
            temp=temporary_directory(),
            state=local / "TATClean",
            user_folders=tuple(folders),
            documents=folders[1],
        )

    def protected_exact(self) -> tuple[Path, ...]:
        return (
            self.profile, self.local, self.roaming, self.program_data, self.windows,
            self.windows / "SoftwareDistribution", self.local / "Roblox",
            self.roaming / "Telegram Desktop", self.roaming / "Telegram Desktop" / "tdata",
        )

    def protected_subtrees(self) -> tuple[Path, ...]:
        user_folders = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")
        system_folders = ("System32", "SysWOW64", "WinSxS", "servicing", "Boot", "SystemApps")
        return (
            self.state,
            *(self.profile / name for name in user_folders),
            *self.user_folders,
            *(self.windows / name for name in system_folders),
            self.local / "Roblox" / "Versions",
            self.roaming / "Telegram Desktop" / "tdata" / "key_datas",
        )


class PathPolicy:
    """A fixed allowlist, protected subtrees, and no reparse traversal.

    The native deleter additionally pins directory handles and verifies final paths;
    these lexical/stat checks alone are intentionally not considered race-proof.
    """

    def __init__(
        self,
        allowed_roots: tuple[Path, ...],
        protected_exact: tuple[Path, ...] = (),
        protected_subtrees: tuple[Path, ...] = (),
        removable_roots: tuple[Path, ...] = (),
    ):
        self.allowed_roots = frozenset(normalize(root) for root in allowed_roots)
        self.protected_exact = frozenset(normalize(root) for root in protected_exact)
        self.protected_subtrees = tuple(protected_subtrees)
        self.removable_roots = frozenset(normalize(root) for root in removable_roots)

    def validate_root(self, root: Path) -> None:
        text = str(root)
        if not root.is_absolute() or text.startswith("\\\\") or ":" in text[3:]:
            raise UnsafePathError("Only absolute, local, non-device cache paths are allowed.")
        canonical = normalize(root)
        if (
            canonical not in self.allowed_roots
            or canonical in self.protected_exact
            or root.parent == root
        ):
            raise UnsafePathError(f"Not an approved cache root: {root}")
        if any(is_within(root, protected) for protected in self.protected_subtrees):
            raise UnsafePathError(f"Protected location: {root}")
        if os.name == "nt":
            from tatclean.native.system import drive_type, is_fixed_local_path
            if not is_fixed_local_path(root) and not (canonical in self.removable_roots and drive_type(root) == 2):
                raise UnsafePathError("Only local fixed/RAM cache volumes are supported; network and removable paths are skipped.")
        self.validate_ancestors(root)

    def validate_ancestors(self, path: Path) -> None:
        validate_ancestry(path)

    def validate_file(self, path: Path, root: Path) -> None:
        if normalize(root) not in self.allowed_roots or not is_within(path, root, include_root=False):
            raise UnsafePathError(f"File is outside the approved cache root: {path}")
        if ":" in str(path)[3:]:
            raise UnsafePathError("Alternate data streams are not cleanup targets.")
        if any(is_within(path, protected) for protected in self.protected_subtrees):
            raise UnsafePathError(f"Protected application or user data: {path}")
        if normalize(path) in self.protected_exact:
            raise UnsafePathError(f"Protected path: {path}")


LOCK_NAMES = frozenset(
    name.casefold()
    for name in (
        "LOCK", "lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket",
        ".parentlock", "parent.lock", "Cookies", "History", "Login Data", "Bookmarks",
        "Preferences", "Secure Preferences", "Local State", "key_datas",
    )
)
LOCK_SUFFIXES = (".lock", ".lck", ".pid")
PROFILE_DIRECTORIES = frozenset(
    name.casefold()
    for name in ("Sessions", "Session Storage", "Local Storage", "IndexedDB", "Extensions")
)


def is_preserved(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    name = path.name.casefold()
    return (
        name in LOCK_NAMES
        or name.endswith(LOCK_SUFFIXES)
        or any(part.casefold() in PROFILE_DIRECTORIES for part in parts[:-1])
    )
