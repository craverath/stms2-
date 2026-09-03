from pathlib import Path
import threading

import pytest

from stms.adapters.persistence.artifact_store import LocalArtifactStore
from stms.adapters.sandbox.native import NativeSandboxFallback
from stms.adapters.sandbox.policy import role_policy
from stms.domain.errors import InfrastructureError, SecurityError
from stms.domain.events import NormalizedEvent
from stms.domain.models import AgentRole, ApprovedUntrackedFile


def test_artifacts_are_atomic_redacted_and_serialized(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path, "run")
    store.write_text("state.json", "token=top-secret")
    assert "top-secret" not in (store.root / "state.json").read_text()
    threads = [threading.Thread(target=store.append_event, args=(NormalizedEvent(run_id="run", event_type="e"),)) for _ in range(10)]
    [thread.start() for thread in threads]; [thread.join() for thread in threads]
    assert len((store.root / "events.jsonl").read_text().splitlines()) == 10


def test_json_redaction_is_recursive_for_secret_keys_and_values(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path, "run")
    store.write_json("state.json", {"environment": {"API_TOKEN": {"literal": "super-secret"}}, "items": [{"nested_secret": "also-secret"}], "message": "password=hidden"})
    content = (store.root / "state.json").read_text()
    assert "super-secret" not in content and "also-secret" not in content and "hidden" not in content


def test_untracked_copy_rejects_escape_and_secret(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path, "run"); (tmp_path / "safe.txt").write_text("safe")
    assert store.copy_approved_untracked(ApprovedUntrackedFile(source="safe.txt", destination="input/safe.txt"), tmp_path / "target").exists()
    (tmp_path / "secret.env").write_text("TOKEN=private")
    with pytest.raises(SecurityError): store.copy_approved_untracked(ApprovedUntrackedFile(source="secret.env", destination="x"), tmp_path / "target")


def test_native_fallback_requires_explicit_equivalent_policy(tmp_path: Path) -> None:
    required = role_policy(AgentRole.IMPLEMENTER, tmp_path, tmp_path / "wt")
    with pytest.raises(InfrastructureError): NativeSandboxFallback(required, explicitly_allowed=False).authorize(required)
    assert NativeSandboxFallback(required, explicitly_allowed=True).authorize(required).role is AgentRole.IMPLEMENTER
