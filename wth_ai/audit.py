"""Append-only audit log for security-relevant actions.

The job manager already records job lifecycle lines at
``<WTH_HOME>/logs/audit.log``. This module is the same sink for *action*
events -- a destructive action that an engagement flag allowed, a policy
override, a kill switch firing -- so the operator can reconstruct afterwards
who did what and under which authorization.

Design constraints, in order:

* **append-only**: opened ``"a"``, never rewritten or truncated; the value of
  an audit trail is that it cannot be edited by the process it records.
* **never fatal**: a full disk or a read-only home must not take down the
  action being audited, so every OS error is swallowed after one stderr note.
* **redacted**: audit lines are logs, and logs get shared. Values pass through
  :func:`wth_ai.redaction.redact_secrets` before they are written.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.redaction import redact_secrets
from wth_ai.workspace import secure_file, wth_home


def audit_path() -> Path:
    return wth_home() / "logs" / "audit.log"


def audit_log(event: str, **fields: Any) -> None:
    """Append one JSON line describing ``event``.

    Extra keyword arguments become fields. ``None`` ends up as JSON ``null``
    rather than being dropped: an absent actor and an unknown actor are
    different findings.
    """
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": str(event),
    }
    for key, value in fields.items():
        if isinstance(value, str):
            record[key] = redact_secrets(value)
        else:
            record[key] = value
    line = json.dumps(record, ensure_ascii=False, sort_keys=False)
    path = audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(redact_secrets(line) + "\n")
        if created:
            secure_file(path)
    except OSError as exc:  # noqa: BLE001 - auditing must never block the action
        print(f"[audit] tidak bisa menulis {path}: {exc}", file=sys.stderr)


def read_audit(limit: int = 100) -> list[dict[str, Any]]:
    """Last ``limit`` audit records, oldest first. Unparseable lines skipped."""
    path = audit_path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue  # a legacy plain-text line from jobs/manager.py
    except OSError:
        return []
    return rows[-limit:]
