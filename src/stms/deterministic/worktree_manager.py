"""The sole authority for STMS Git mutations, using argv-only Git commands."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
from hashlib import sha256
from typing import Mapping

from stms.domain.errors import InfrastructureError


def default_worktrees_root(repository: Path) -> Path:
    """A deterministic root outside the repository, keyed by its resolved path.

    Worktrees living inside the repository directory (e.g. under ``.stms/``) show
    up as untracked/nested-git paths in ``git status`` unless painstakingly kept
    in sync with ``.gitignore``. An external, hash-keyed root avoids that entirely
    while staying reproducible across processes for the same repository.
    """
    digest = sha256(str(repository.resolve()).encode()).hexdigest()[:16]
    return (Path(tempfile.gettempdir()) / "stms-worktrees" / digest).resolve()


class GitWorktreeManager:
    def __init__(self, repository: Path, *, worktrees_root: Path | None = None) -> None:
        self.repository = repository.resolve()
        self.worktrees_root = (worktrees_root or default_worktrees_root(self.repository)).resolve()
        self._integration: Path | None = None
        self._tasks: dict[str, Path] = {}
        self._run_id: str | None = None

    def _git(self, args: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["git", *args], cwd=cwd or self.repository, text=True, capture_output=True)
        if check and result.returncode:
            message = result.stderr.strip() or result.stdout.strip() or "git command failed"
            raise InfrastructureError(f"Git {' '.join(args)} failed: {message}", "Resolve the Git repository state and retry the operation.")
        return result

    def create_integration(self, run_id: str, commit_base: str) -> Path:
        branch = f"stms/{run_id}/integration"; path = self._worktree_path(run_id, "integration")
        if path.exists(): self._integration = path; self._run_id = run_id; return path
        self._git(["branch", branch, commit_base]); self._git(["worktree", "add", str(path), branch])
        self._integration = path; self._run_id = run_id; return path

    def create_task(self, run_id: str, task_id: str, commit_base: str | None = None) -> Path:
        if self._integration is None: raise InfrastructureError("Integration worktree is not initialized.", "Create the integration worktree before task worktrees.")
        branch = f"stms/{run_id}/task-{task_id}"; path = self._worktree_path(run_id, f"task-{task_id}")
        if path.exists(): self._tasks[task_id] = path; return path
        # A wave must explicitly pass the integration HEAD, not the original base.
        start = commit_base or self._git(["rev-parse", "HEAD"], cwd=self._integration).stdout.strip()
        self._git(["branch", branch, start]); self._git(["worktree", "add", str(path), branch]); self._tasks[task_id] = path
        return path

    def commit_task(self, task_id: str, message: str) -> str:
        path = self._task(task_id); self._git(["add", "--all"], cwd=path)
        if self._git(["diff", "--cached", "--quiet"], cwd=path, check=False).returncode == 0:
            raise InfrastructureError("Task has no staged changes to commit.", "Implement the approved task before requesting integration.")
        self._git(["commit", "-m", message], cwd=path)
        return self._git(["rev-parse", "HEAD"], cwd=path).stdout.strip()

    def prepare_task_commit(self, task_id: str) -> dict[str, str]:
        """Stage and record the exact tree before issuing the irreversible commit."""
        path = self._task(task_id)
        branch = self._git(["branch", "--show-current"], cwd=path).stdout.strip()
        parent = self._git(["rev-parse", "HEAD"], cwd=path).stdout.strip()
        self._git(["add", "--all"], cwd=path)
        tree = self._git(["write-tree"], cwd=path).stdout.strip()
        return {"branch": branch, "parent": parent, "tree": tree}

    def task_commit_metadata(self, task_id: str) -> dict[str, str]:
        path = self._task(task_id)
        sha = self._git(["rev-parse", "HEAD"], cwd=path).stdout.strip()
        parent = self._git(["rev-parse", "HEAD^"], cwd=path).stdout.strip()
        diff = self._git(["diff", "HEAD^", "HEAD"], cwd=path).stdout
        return {"sha": sha, "parent": parent, "diff_digest": sha256(diff.encode()).hexdigest()}

    def reconciled_task_commit(self, task_id: str, expected: Mapping[str, str] | None = None) -> str | None:
        """Return a completed task commit after a crash before its checkpoint."""
        if self._run_id is None:
            return None
        branch = f"stms/{self._run_id}/task-{task_id}"
        exists = self._git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False)
        if exists.returncode:
            return None
        if expected is None:
            return None
        sha = self._git(["rev-parse", branch]).stdout.strip()
        parent = self._git(["rev-parse", f"{branch}^"], check=False)
        diff = self._git(["diff", f"{branch}^", branch], check=False).stdout
        tree = self._git(["rev-parse", f"{branch}^{{tree}}"], check=False)
        if (expected.get("sha") and sha != expected.get("sha")) or parent.returncode or parent.stdout.strip() != expected.get("parent") or (expected.get("tree") and tree.stdout.strip() != expected.get("tree")) or (expected.get("diff_digest") and sha256(diff.encode()).hexdigest() != expected.get("diff_digest")):
            return None
        return sha

    def integrate_task(self, task_id: str) -> None:
        integration = self._require_integration(); branch = f"stms/{self._run_id}/task-{task_id}"
        commit = self._git(["rev-parse", branch]).stdout.strip()
        outcome = self._git(["cherry-pick", commit], cwd=integration, check=False)
        if outcome.returncode:
            self._git(["cherry-pick", "--abort"], cwd=integration, check=False)
            raise InfrastructureError("Task integration has a conflict.", "Return the conflict details to the responsible implementer; retry after correction.")

    def integration_metadata(self) -> dict[str, str]:
        integration = self._require_integration()
        sha = self._git(["rev-parse", "HEAD"], cwd=integration).stdout.strip()
        parent = self._git(["rev-parse", "HEAD^"], cwd=integration).stdout.strip()
        diff = self._git(["diff", "HEAD^", "HEAD"], cwd=integration).stdout
        return {"sha": sha, "parent": parent, "diff_digest": sha256(diff.encode()).hexdigest()}

    def prepare_task_integration(self, task_id: str) -> dict[str, str]:
        integration = self._require_integration()
        branch = f"stms/{self._run_id}/task-{task_id}"
        commit = self._git(["rev-parse", branch]).stdout.strip()
        parent = self._git(["rev-parse", "HEAD"], cwd=integration).stdout.strip()
        diff = self._git(["diff", f"{commit}^", commit]).stdout
        return {"branch": branch, "task_commit": commit, "parent": parent, "diff_digest": sha256(diff.encode()).hexdigest()}

    def reconciled_task_integration(self, task_id: str, expected: Mapping[str, str] | None = None) -> bool:
        if self._run_id is None or self._integration is None:
            return False
        if expected is None:
            return False
        sha = expected.get("sha")
        if sha:
            candidate = sha
        else:
            candidate = self._git(["rev-parse", "HEAD"], cwd=self._integration).stdout.strip()
        if self._git(["merge-base", "--is-ancestor", candidate, "HEAD"], cwd=self._integration, check=False).returncode:
            return False
        parent = self._git(["rev-parse", f"{candidate}^"], cwd=self._integration, check=False)
        diff = self._git(["diff", f"{candidate}^", candidate], cwd=self._integration, check=False).stdout
        return parent.returncode == 0 and parent.stdout.strip() == expected.get("parent") and sha256(diff.encode()).hexdigest() == expected.get("diff_digest")

    def commit_integration(self, message: str) -> str | None:
        """Commit a human/agent correction made directly in the integration tree."""
        integration = self._require_integration()
        self._git(["add", "--all"], cwd=integration)
        if self._git(["diff", "--cached", "--quiet"], cwd=integration, check=False).returncode == 0:
            return None
        self._git(["commit", "-m", message], cwd=integration)
        return self._git(["rev-parse", "HEAD"], cwd=integration).stdout.strip()

    def base_unchanged(self, branch_base: str, commit_base: str) -> bool:
        return self._git(["rev-parse", branch_base]).stdout.strip() == commit_base

    def squash_merge(self, *, branch_base: str, commit_base: str, summary: str, run_id: str) -> str:
        if not self.base_unchanged(branch_base, commit_base):
            raise InfrastructureError("Base branch changed since the run started.", "Pause the run and ask a human to decide how to update the base.")
        integration = self._require_integration(); branch = f"stms/{run_id}/integration"
        # The repository checkout is the original branch and is untouched until this method.
        self._git(["checkout", branch_base])
        outcome = self._git(["merge", "--squash", branch], check=False)
        if outcome.returncode:
            self._git(["merge", "--abort"], check=False)
            raise InfrastructureError("Final squash merge has a conflict.", "Pause for human conflict resolution; no automatic rebase is performed.")
        self._git(["commit", "-m", f"stms: {summary} [{run_id}]"])
        return self._git(["rev-parse", "HEAD"]).stdout.strip()

    def reconciled_squash(self, *, branch_base: str, summary: str, run_id: str) -> str | None:
        """Recognize a commit completed before a process crashed after Git returned."""
        message = f"stms: {summary} [{run_id}]"
        subject = self._git(["log", "-1", "--format=%s", branch_base]).stdout.strip()
        if subject != message:
            return None
        return self._git(["rev-parse", branch_base]).stdout.strip()

    def reconcile_pending_squash(self, *, branch_base: str, commit_base: str, run_id: str) -> str:
        """Clean only STMS's own interrupted squash state; never discard user diffs."""
        git_dir = self._git(["rev-parse", "--git-dir"]).stdout.strip()
        merge_head = (self.repository / git_dir / "MERGE_HEAD").exists()
        status = self._git(["status", "--porcelain"]).stdout.strip()
        if not merge_head and not status:
            return "clean"
        integration = f"stms/{run_id}/integration"
        expected = self._git(["diff", f"{commit_base}..{integration}"], check=False)
        staged = self._git(["diff", "--cached"], check=False)
        unstaged = self._git(["diff"], check=False)
        if merge_head or (not unstaged.stdout.strip() and staged.stdout.strip() == expected.stdout.strip()):
            self._git(["merge", "--abort"], check=False)
            # A squash without MERGE_HEAD may need an explicit index/worktree reset.
            if self._git(["status", "--porcelain"]).stdout.strip():
                self._git(["reset", "--merge", commit_base])
            return "cleaned"
        return "ambiguous"

    def cleanup_success(self) -> None:
        paths = [*self._tasks.values(), *([self._integration] if self._integration else [])]
        for path in paths: self._git(["worktree", "remove", "--force", str(path)], check=False)
        if self._run_id:
            for task_id in self._tasks: self._git(["branch", "-D", f"stms/{self._run_id}/task-{task_id}"], check=False)
            self._git(["branch", "-D", f"stms/{self._run_id}/integration"], check=False)

    def _worktree_path(self, run_id: str, name: str) -> Path:
        return self.worktrees_root / run_id / name

    def _require_integration(self) -> Path:
        if self._integration is None: raise InfrastructureError("Integration worktree is not initialized.", "Create it before integrating task branches.")
        return self._integration

    def _task(self, task_id: str) -> Path:
        if task_id not in self._tasks: raise InfrastructureError(f"Unknown task worktree {task_id}.", "Create the task worktree before committing it.")
        return self._tasks[task_id]
