from pathlib import Path
import subprocess

from stms.deterministic.worktree_manager import GitWorktreeManager, default_worktrees_root


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True).stdout.strip()


def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"; repo.mkdir(); git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "test@example.com"); git(repo, "config", "user.name", "Test")
    (repo / "base.txt").write_text("base\n"); git(repo, "add", "base.txt"); git(repo, "commit", "-m", "base")
    return repo


def test_worktrees_integrate_then_squash_once(tmp_path: Path) -> None:
    repo = repository(tmp_path); base = git(repo, "rev-parse", "HEAD"); manager = GitWorktreeManager(repo)
    integration = manager.create_integration("run", base); task = manager.create_task("run", "one")
    (task / "feature.txt").write_text("feature\n"); manager.commit_task("one", "temporary task"); manager.integrate_task("one")
    assert not (repo / "feature.txt").exists()
    merged = manager.squash_merge(branch_base="main", commit_base=base, summary="feature", run_id="run")
    assert (repo / "feature.txt").exists() and git(repo, "rev-parse", "HEAD") == merged
    assert git(repo, "rev-list", "--count", "main") == "2"
    manager.cleanup_success()
    assert not integration.exists() and not task.exists()


def test_changed_base_refuses_final_merge(tmp_path: Path) -> None:
    repo = repository(tmp_path); base = git(repo, "rev-parse", "HEAD"); manager = GitWorktreeManager(repo)
    manager.create_integration("run", base); task = manager.create_task("run", "one")
    (task / "feature.txt").write_text("feature\n"); manager.commit_task("one", "temporary task"); manager.integrate_task("one")
    (repo / "other.txt").write_text("other\n"); git(repo, "add", "other.txt"); git(repo, "commit", "-m", "other")
    from stms.domain.errors import InfrastructureError
    import pytest
    with pytest.raises(InfrastructureError): manager.squash_merge(branch_base="main", commit_base=base, summary="feature", run_id="run")


def test_task_reconciliation_rejects_same_subject_with_different_commit(tmp_path: Path) -> None:
    repo = repository(tmp_path); base = git(repo, "rev-parse", "HEAD"); manager = GitWorktreeManager(repo)
    manager.create_integration("run", base); task = manager.create_task("run", "one")
    (task / "feature.txt").write_text("first\n"); manager.commit_task("one", "stms task one")
    metadata = manager.task_commit_metadata("one")
    (task / "feature.txt").write_text("second\n"); git(task, "add", "feature.txt"); git(task, "commit", "--amend", "-m", "stms task one")
    assert manager.reconciled_task_commit("one", metadata) is None


def test_worktrees_live_outside_the_repository_and_keep_git_status_clean(tmp_path: Path) -> None:
    repo = repository(tmp_path); base = git(repo, "rev-parse", "HEAD")
    manager = GitWorktreeManager(repo, worktrees_root=tmp_path / "external-worktrees")
    integration = manager.create_integration("run", base)
    task = manager.create_task("run", "one")
    assert repo not in integration.parents and repo not in task.parents
    assert git(repo, "status", "--porcelain") == ""


def test_default_worktrees_root_is_deterministic_per_repository_and_outside_it(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    first = GitWorktreeManager(repo).worktrees_root
    second = GitWorktreeManager(repo).worktrees_root
    assert first == second
    assert repo.resolve() not in first.parents and first != repo.resolve()
    assert first == default_worktrees_root(repo)


def test_reconciles_commit_and_cherry_pick_after_crash_before_checkpoint(tmp_path: Path) -> None:
    repo = repository(tmp_path); base = git(repo, "rev-parse", "HEAD"); manager = GitWorktreeManager(repo)
    manager.create_integration("run", base); task = manager.create_task("run", "one")
    (task / "feature.txt").write_text("feature\n")
    commit_before = manager.prepare_task_commit("one")
    manager.commit_task("one", "stms task one")  # crash point: operation still pending
    assert manager.reconciled_task_commit("one", commit_before)
    integration_before = manager.prepare_task_integration("one")
    manager.integrate_task("one")  # crash point: operation still pending
    assert manager.reconciled_task_integration("one", integration_before)
