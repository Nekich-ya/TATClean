from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

from tatclean.core.gaming_models import RestorePointInfo
from tatclean.core.models import TaskProgress
from tatclean.core.tasks import TaskContext
from tatclean.native.system import is_admin, system_directory


class RestorePoints:
    """Verify a real System Restore point; never change protection/frequency policy."""

    def __init__(self, runner: Any):
        self.runner = runner
        self.executable = system_directory() / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        self.cached: RestorePointInfo | None = None
        self._cached_at = 0.0

    def _powershell(self, body: str, context: TaskContext) -> dict[str, Any]:
        script = (
            "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
            "[Console]::Write([char]0xFEFF);"
            "try { " + body + " } catch { "
            "@{ok=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress;exit 1 }"
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        output = self.runner.run((str(self.executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded), context, timeout=180)
        try:
            result = json.loads(output.text.lstrip("\ufeff"))
            if not isinstance(result, dict):
                raise ValueError("Restore-point output is not an object.")
        except (ValueError, TypeError) as exc:
            raise OSError(f"System Restore returned unverified output: {output.text[:2000]}") from exc
        if output.returncode or not result.get("ok"):
            raise OSError("No verified restore point was created. No gaming tweaks were applied. "
                          + str(result.get("error", "Check System Protection, administrative rights and the Windows restore-point frequency limit.")))
        return result

    def ensure(self, context: TaskContext, *, force_new: bool = False) -> RestorePointInfo:
        if not is_admin():
            raise PermissionError("Administrator access is required to create/verify a System Restore point.")
        context.checkpoint()
        if self.cached is not None and not force_new and time.monotonic() - self._cached_at < 3600:
            # Reuse only this session's freshly created point, and verify it still exists.
            sequence = self.cached.sequence
            result = self._powershell(
                f"$p=Get-CimInstance -Namespace root/default -ClassName SystemRestore |Where-Object {{$_.SequenceNumber -eq {sequence}}};"
                "if(-not $p){throw 'The session restore point was removed.'};"
                "@{ok=$true;sequence=[int]$p.SequenceNumber;description=[string]$p.Description;created=[string]$p.CreationTime}|ConvertTo-Json -Compress",
                context,
            )
            if result.get("description") != self.cached.description:
                raise ValueError("Restore-point identity changed; gaming changes are blocked.")
            return self.cached
        description = "TAT Clean Gaming " + uuid.uuid4().hex
        context.report(TaskProgress("Creating and verifying a Windows System Restore point…"), force=True)
        result = self._powershell(
            "$before=@(Get-CimInstance -Namespace root/default -ClassName SystemRestore |Select-Object -ExpandProperty SequenceNumber);"
            f"Checkpoint-Computer -Description '{description}' -RestorePointType MODIFY_SETTINGS -ErrorAction Stop -WarningAction Stop;"
            f"$p=Get-CimInstance -Namespace root/default -ClassName SystemRestore |Where-Object {{$_.Description -eq '{description}' -and $_.SequenceNumber -notin $before}}|Sort-Object SequenceNumber -Descending|Select-Object -First 1;"
            "if(-not $p){throw 'Windows did not create a new restore point. System Protection may be disabled or the 24-hour quota may apply.'};"
            "@{ok=$true;sequence=[int]$p.SequenceNumber;description=[string]$p.Description;created=[string]$p.CreationTime}|ConvertTo-Json -Compress",
            context,
        )
        if result.get("description") != description or not isinstance(result.get("sequence"), int) or result["sequence"] <= 0:
            raise ValueError("Restore-point creation could not be verified; gaming changes are blocked.")
        self.cached = RestorePointInfo(result["sequence"], description, str(result.get("created", "")))
        self._cached_at = time.monotonic()
        context.log(f"Verified restore point #{self.cached.sequence}. Exact tweak baselines will also be saved.")
        return self.cached
