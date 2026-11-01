from __future__ import annotations

import ctypes
import hashlib
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from pathlib import Path

from tatclean.core.models import DeleteOutcome, FileRecord, SkipReason, TaskProgress
from tatclean.core.paths import PathPolicy, UnsafePathError, normalize
from tatclean.core.tasks import TaskContext
from tatclean.native.winapi import (
    DELETE,
    FILE_ATTRIBUTE_DIRECTORY,
    FILE_ATTRIBUTE_READONLY,
    FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_LIST_DIRECTORY,
    FILE_READ_ATTRIBUTES,
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    Handle,
    Kernel,
    bind,
    check_bool,
    filetime_ns,
)


class FileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("creation", wintypes.FILETIME),
        ("access", wintypes.FILETIME),
        ("write", wintypes.FILETIME),
        ("volume", wintypes.DWORD),
        ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD),
        ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD),
        ("index_low", wintypes.DWORD),
    ]


class FileIdInformation(ctypes.Structure):
    _fields_ = [("volume", ctypes.c_ulonglong), ("identifier", ctypes.c_ubyte * 16)]


class FileDispositionInformation(ctypes.Structure):
    _fields_ = [("delete", ctypes.c_ubyte)]


def error_reason(error: OSError) -> SkipReason:
    code = getattr(error, "winerror", None)
    if isinstance(error, FileNotFoundError) or code in (2, 3):
        return SkipReason.MISSING
    if code in (32, 33, 1224):
        return SkipReason.LOCKED
    if isinstance(error, PermissionError) or code in (5, 1314):
        return SkipReason.PERMISSION
    return SkipReason.ERROR


class PinnedRoot:
    def __init__(self, backend: WindowsFileDeleter, root: Path, policy: PathPolicy):
        self.backend = backend
        self.root = root
        self.policy = policy
        self._stack = ExitStack()

    def __enter__(self) -> PinnedRoot:
        self.policy.validate_root(self.root)
        try:
            # Excluding FILE_SHARE_DELETE pins every ancestor, including the volume root.
            # An attacker cannot substitute a junction/rename an ancestor during deletion.
            for directory in reversed((self.root, *self.root.parents)):
                self._stack.enter_context(self.backend.pin_directory(directory))
        except BaseException:
            self._stack.close()
            raise
        return self

    def __exit__(self, *_):
        self._stack.close()


class WindowsFileDeleter:
    """Delete the verified file through the same exclusive Win32 handle.

    No chmod, take-ownership, recursive removal, symlink following, or delayed
    reboot deletion is used. A busy/read-only file is a skip, not a problem to defeat.
    """

    def __init__(self):
        self.kernel = Kernel()
        self.get_information = bind(
            self.kernel.dll, "GetFileInformationByHandle",
            [wintypes.HANDLE, ctypes.POINTER(FileInformation)], wintypes.BOOL,
        )
        self.get_information_ex = bind(
            self.kernel.dll, "GetFileInformationByHandleEx",
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL,
        )
        self.set_information = bind(
            self.kernel.dll, "SetFileInformationByHandle",
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL,
        )
        self.read_file = bind(self.kernel.dll, "ReadFile",
                              [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes.BOOL)

    def _verified_information(self, handle: Handle, record: FileRecord, path: Path) -> DeleteOutcome | None:
        information = self.information(handle)
        if information.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            return DeleteOutcome(False, SkipReason.REPARSE)
        if information.attributes & FILE_ATTRIBUTE_DIRECTORY:
            return DeleteOutcome(False, SkipReason.OUTSIDE_SCOPE)
        if information.links != 1:
            return DeleteOutcome(False, SkipReason.HARD_LINK)
        if self.kernel.get_final_path(handle) != normalize(path):
            return DeleteOutcome(False, SkipReason.OUTSIDE_SCOPE)
        size = (information.size_high << 32) | information.size_low
        mtime = filetime_ns(information.write)
        identifiers = {(information.index_high << 32) | information.index_low}
        volumes = {int(information.volume)}
        extended = FileIdInformation()
        if self.get_information_ex(handle.value, 18, ctypes.byref(extended), ctypes.sizeof(extended)):
            identifiers.add(int.from_bytes(bytes(extended.identifier), "little"))
            volumes.add(int(extended.volume))
        if (size != record.size or mtime != record.mtime_ns or not record.inode
                or record.inode not in identifiers or (record.device and record.device not in volumes)):
            return DeleteOutcome(False, SkipReason.CHANGED)
        return None

    def _hash_handle(self, handle: Handle, record: FileRecord, context: TaskContext) -> str:
        digest, total = hashlib.sha256(), 0
        buffer = ctypes.create_string_buffer(1024 * 1024)
        while True:
            context.checkpoint()
            count = wintypes.DWORD()
            check_bool(self.read_file(handle.value, buffer, len(buffer), ctypes.byref(count), None))
            if not count.value:
                break
            total += count.value
            digest.update(buffer.raw[:count.value])
            context.report(TaskProgress(f"Hashing crash dump: {Path(record.path).name}", current=total, total=record.size))
        if total != record.size:
            raise ValueError("The dump changed while it was being hashed.")
        return digest.hexdigest()

    def hash_file(self, record: FileRecord, root: Path, policy: PathPolicy, context: TaskContext) -> str:
        with self.pin_root(root, policy) as pinned:
            path = Path(record.path)
            pinned.policy.validate_file(path, root)
            with self.pin_subdirectory(path.parent, pinned), self.kernel.open_file(path, 0x80000000 | FILE_READ_ATTRIBUTES, 0, FILE_FLAG_OPEN_REPARSE_POINT) as handle:
                invalid = self._verified_information(handle, record, path)
                if invalid:
                    raise ValueError(f"Dump cannot be verified: {invalid.reason}")
                return self._hash_handle(handle, record, context)

    @contextmanager
    def pin_keeper(self, record: FileRecord, root: Path, policy: PathPolicy, context: TaskContext):
        with self.pin_root(root, policy) as pinned:
            path = Path(record.path)
            pinned.policy.validate_file(path, root)
            with self.pin_subdirectory(path.parent, pinned), self.kernel.open_file(path, 0x80000000 | FILE_READ_ATTRIBUTES, 0, FILE_FLAG_OPEN_REPARSE_POINT) as handle:
                invalid = self._verified_information(handle, record, path)
                if invalid or self._hash_handle(handle, record, context) != record.content_hash:
                    raise ValueError("The retained crash-dump copy is missing or changed. No duplicate will be deleted.")
                yield  # Keep a verified, non-deletable/non-writable copy open throughout deletion.

    def information(self, handle: Handle) -> FileInformation:
        information = FileInformation()
        check_bool(self.get_information(handle.value, ctypes.byref(information)))
        return information

    def pin_directory(self, directory: Path) -> Handle:
        handle = self.kernel.open_file(
            directory, FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY, FILE_SHARE_READ | FILE_SHARE_WRITE,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        )
        try:
            information = self.information(handle)
            if not information.attributes & FILE_ATTRIBUTE_DIRECTORY:
                raise UnsafePathError(f"Expected a directory: {directory}")
            if information.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                raise UnsafePathError(f"Reparse directory refused: {directory}")
            if self.kernel.get_final_path(handle) != normalize(directory):
                raise UnsafePathError(f"Directory resolves outside its declared path: {directory}")
        except BaseException:
            handle.close()
            raise
        return handle

    def pin_root(self, root: Path, policy: PathPolicy) -> PinnedRoot:
        return PinnedRoot(self, root, policy)

    def pin_subdirectory(self, directory: Path, pinned: PinnedRoot) -> ExitStack:
        stack = ExitStack()
        try:
            relative = directory.relative_to(pinned.root)
            current = pinned.root
            for component in relative.parts:
                current /= component
                stack.enter_context(self.pin_directory(current))
        except BaseException:
            stack.close()
            raise
        return stack

    def delete(self, record: FileRecord, pinned: PinnedRoot, cutoff_ns: int, context: TaskContext | None = None) -> DeleteOutcome:
        path = Path(record.path)
        try:
            pinned.policy.validate_file(path, pinned.root)
            relative = path.relative_to(pinned.root)
            with ExitStack() as stack:
                directory = pinned.root
                for component in relative.parts[:-1]:
                    directory /= component
                    stack.enter_context(self.pin_directory(directory))
                handle = stack.enter_context(
                    self.kernel.open_file(
                        path, DELETE | FILE_READ_ATTRIBUTES | (0x80000000 if record.content_hash else 0), 0, FILE_FLAG_OPEN_REPARSE_POINT
                    )
                )
                information = self.information(handle)
                invalid = self._verified_information(handle, record, path)
                if invalid:
                    return invalid
                if information.attributes & FILE_ATTRIBUTE_READONLY:
                    return DeleteOutcome(False, SkipReason.PERMISSION, "Read-only file preserved")
                mtime = filetime_ns(information.write)
                if mtime >= cutoff_ns:
                    return DeleteOutcome(False, SkipReason.RECENT)
                if record.content_hash and self._hash_handle(handle, record, context or TaskContext()) != record.content_hash:
                    return DeleteOutcome(False, SkipReason.CHANGED, "Duplicate content changed since preview.")
                disposition = FileDispositionInformation(1)
                check_bool(
                    self.set_information(
                        handle.value, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)
                    )
                )
            return DeleteOutcome(True)
        except UnsafePathError as exc:
            return DeleteOutcome(False, SkipReason.OUTSIDE_SCOPE, str(exc))
        except OSError as exc:
            return DeleteOutcome(False, error_reason(exc), str(exc))
        except ValueError as exc:
            return DeleteOutcome(False, SkipReason.CHANGED, str(exc))
