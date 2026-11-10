"""Guard: every full-length commit hash written in a test file must resolve here.

This repository's history was rewritten from a private one, so a commit hash
copied from the old history does not exist in it. A test that pins such a hash
either fails (``git diff <hash>`` on a missing object) or, worse, silently
SKIPS because its own "is the base in this repo?" check answers no. Two files
carried four such hashes until they were repointed to their public twins; this
test keeps the class from coming back.

Contract (each clause is a cell below, run against a scratch git repo):

  C1. A 40-hex string that ``git cat-file -e <hash>^{commit}`` resolves passes.
  C2. One that does not resolve and is not in EXCEPTIONS fails, and the
      message names the file, the line number and the hash.
  C3. A stale EXCEPTIONS entry fails: the (path, hash) pair no longer occurs in
      that file, or the hash now resolves. An exception that outlives its reason
      hides the next member of the class.
  C4. On a shallow clone the real-scan cell skips and says so: an object absent
      from a shallow clone proves nothing.
  C5. The scanner's own failure raises and is never a pass: ``git`` not
      runnable, ``git ls-files`` returning no files for the scanned roots, or a
      tracked file that cannot be read. An empty scan set fails loudly.

Files are enumerated with ``git ls-files``, never from a hand-written list.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Directories scanned, as git pathspecs.
ROOTS = ("tests/", "src/forum/tests/")

# This file is excluded from the scan: its own literals ARE the table.
SELF = "tests/test_full_length_hashes_resolve.py"

# Word-bounded 40-character lowercase hex strings.
HASH_RE = re.compile(r"\b[0-9a-f]{40}\b")

# (repo-relative path, hash) -> why this hash is allowed not to resolve.
EXCEPTIONS = {
    ("tests/test_a6_attempts_ledger_metricgate.py", "0123456789abcdef0123456789abcdef01234567"): (
        "synthetic build id in a fixture"
    ),
    ("tests/test_a6_metricgate_emitter_metricgate.py", "0123456789abcdef0123456789abcdef01234567"): (
        "synthetic build id in a fixture"
    ),
    ("tests/test_health_series.py", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"): (
        "deliberately fake value"
    ),
    ("tests/test_warm_briefing_erase_safety.py", "901f37ab095cc32aaffb3ddc6fc0f0d729dd5c70"): (
        "historical note in a comment; a hash from the project's private history, "
        "which this test does not resolve"
    ),
    ("tests/test_owner_launcher_3663.py", "cf86878b2fe5a17ea914aeb073744cfa4cf5ce85"): (
        "historical note in a comment; a hash from the project's private history, "
        "which this test does not resolve"
    ),
    ("tests/test_start_daemon_probe_branch_logging.py", "4ad008cc961e3d37a707179b7407f78f3f86548d"): (
        "historical note in a comment; a hash from the project's private history, "
        "which this test does not resolve"
    ),
}

GIT = "git"


class ScanError(RuntimeError):
    """The scanner itself failed. Never a pass."""


def _run_git(repo, *args):
    try:
        return subprocess.run(
            [GIT, "-C", str(repo), *args], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise ScanError(f"git is not runnable: {exc}") from exc


def is_shallow(repo) -> bool:
    proc = _run_git(repo, "rev-parse", "--is-shallow-repository")
    if proc.returncode != 0:
        raise ScanError(f"git rev-parse --is-shallow-repository failed: {proc.stderr.strip()}")
    return proc.stdout.strip() == "true"


def tracked_files(repo) -> list[str]:
    proc = _run_git(repo, "ls-files", "-z", "--", *[f"{r}*.py" for r in ROOTS])
    if proc.returncode != 0:
        raise ScanError(f"git ls-files failed: {proc.stderr.strip()}")
    files = [p for p in proc.stdout.split("\0") if p and p != SELF]
    if not files:
        raise ScanError(f"git ls-files returned no files under {ROOTS}; an empty scan proves nothing")
    return files


def scan(repo, files) -> list[tuple[str, int, str]]:
    """Every (path, line number, hash) occurrence in ``files``."""
    found = []
    for rel in files:
        try:
            text = (Path(repo) / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ScanError(f"cannot read tracked file {rel}: {exc}") from exc
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in HASH_RE.finditer(line):
                found.append((rel, lineno, m.group(0)))
    return found


def resolves(repo, h) -> bool:
    return _run_git(repo, "cat-file", "-e", f"{h}^{{commit}}").returncode == 0


def problems(repo, exceptions) -> list[str]:
    """Every violation, as a message. An empty list is a pass."""
    occurrences = scan(repo, tracked_files(repo))
    out = []
    for rel, lineno, h in occurrences:
        if (rel, h) in exceptions or resolves(repo, h):
            continue
        out.append(f"{rel}:{lineno}: {h} does not resolve to a commit in this repository")
    present = {(rel, h) for rel, _, h in occurrences}
    for (rel, h), reason in exceptions.items():
        if (rel, h) not in present:
            out.append(f"stale EXCEPTIONS entry {rel} {h} ({reason}): no longer occurs in that file")
        elif resolves(repo, h):
            out.append(f"stale EXCEPTIONS entry {rel} {h} ({reason}): the hash now resolves")
    return out


# ---------------------------------------------------------------------------
# scratch-repo helpers
# ---------------------------------------------------------------------------

FAKE = "f" * 40
FAKE2 = "e" * 40


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _make_repo(tmp_path, files):
    """A scratch repo with one commit holding ``files`` ({relpath: text}); returns (path, sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c1")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, sha


class TestC1Resolving:
    def test_resolving_hash_passes(self, tmp_path):
        repo, _ = _make_repo(tmp_path, {"tests/test_a.py": "X = 1\n"})
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        (repo / "tests/test_b.py").write_text(f'PIN = "{sha}"\n')
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "c2")
        assert problems(repo, {}) == []

    def test_non_commit_object_does_not_count_as_resolving(self, tmp_path):
        # A blob hash exists as an object but is not a commit; the guard asks for a commit.
        repo, _ = _make_repo(tmp_path, {"tests/test_a.py": "X = 1\n"})
        blob = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD:tests/test_a.py"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        (repo / "tests/test_b.py").write_text(f'PIN = "{blob}"\n')
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "c2")
        assert len(problems(repo, {})) == 1


class TestC2Unresolved:
    def test_unresolved_hash_fails_naming_file_line_and_hash(self, tmp_path):
        repo, _ = _make_repo(tmp_path, {"tests/test_a.py": f'# one\n\nPIN = "{FAKE}"\n'})
        msgs = problems(repo, {})
        assert len(msgs) == 1
        assert "tests/test_a.py:3" in msgs[0]
        assert FAKE in msgs[0]

    def test_src_forum_tests_root_is_scanned(self, tmp_path):
        repo, _ = _make_repo(tmp_path, {"src/forum/tests/test_f.py": f'PIN = "{FAKE}"\n'})
        msgs = problems(repo, {})
        assert len(msgs) == 1 and "src/forum/tests/test_f.py:1" in msgs[0]

    def test_excepted_pair_is_allowed(self, tmp_path):
        repo, _ = _make_repo(tmp_path, {"tests/test_a.py": f'PIN = "{FAKE}"\n'})
        assert problems(repo, {("tests/test_a.py", FAKE): "fixture"}) == []

    def test_exception_is_per_file_and_per_hash(self, tmp_path):
        repo, _ = _make_repo(
            tmp_path,
            {"tests/test_a.py": f'PIN = "{FAKE}"\n', "tests/test_b.py": f'PIN = "{FAKE}"\nQ = "{FAKE2}"\n'},
        )
        msgs = problems(repo, {("tests/test_a.py", FAKE): "fixture"})
        assert len(msgs) == 2
        assert any("tests/test_b.py:1" in m and FAKE in m for m in msgs)
        assert any("tests/test_b.py:2" in m and FAKE2 in m for m in msgs)

    def test_only_word_bounded_40_hex_matches(self, tmp_path):
        text = f'A = "{"a" * 39}"\nB = "{"a" * 41}"\nC = "{"A" * 40}"\n'
        repo, _ = _make_repo(tmp_path, {"tests/test_a.py": text})
        assert problems(repo, {}) == []

    def test_this_file_is_excluded_from_the_scan(self, tmp_path):
        repo, _ = _make_repo(tmp_path, {SELF: f'PIN = "{FAKE}"\n', "tests/test_a.py": "X = 1\n"})
        assert problems(repo, {}) == []


class TestC3StaleExceptions:
    def test_exception_whose_hash_left_the_file_fails(self, tmp_path):
        repo, _ = _make_repo(tmp_path, {"tests/test_a.py": "X = 1\n"})
        msgs = problems(repo, {("tests/test_a.py", FAKE): "gone"})
        assert len(msgs) == 1 and "stale" in msgs[0] and FAKE in msgs[0]

    def test_exception_for_a_file_that_is_gone_fails(self, tmp_path):
        repo, _ = _make_repo(tmp_path, {"tests/test_a.py": "X = 1\n"})
        msgs = problems(repo, {("tests/test_nope.py", FAKE): "gone"})
        assert len(msgs) == 1 and "stale" in msgs[0]

    def test_exception_whose_hash_now_resolves_fails(self, tmp_path):
        repo, sha = _make_repo(tmp_path, {"tests/test_a.py": "X = 1\n"})
        (repo / "tests/test_b.py").write_text(f'PIN = "{sha}"\n')
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "c2")
        msgs = problems(repo, {("tests/test_b.py", sha): "was private"})
        assert len(msgs) == 1 and "stale" in msgs[0] and "now resolves" in msgs[0]


class TestC4Shallow:
    def test_shallow_clone_is_detected(self, tmp_path):
        src, _ = _make_repo(tmp_path, {"tests/test_a.py": "X = 1\n"})
        (src / "tests/test_a.py").write_text("X = 2\n")
        _git(src, "commit", "-aq", "-m", "c2")
        clone = tmp_path / "shallow"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{src}", str(clone)],
            check=True,
            capture_output=True,
        )
        assert is_shallow(clone) is True
        assert is_shallow(src) is False


class TestC5ScannerFailureRaises:
    def test_git_not_runnable_raises(self, tmp_path, monkeypatch):
        repo, _ = _make_repo(tmp_path, {"tests/test_a.py": "X = 1\n"})
        monkeypatch.setattr(sys.modules[__name__], "GIT", "git-does-not-exist-xyz")
        with pytest.raises(ScanError, match="not runnable"):
            problems(repo, {})

    def test_empty_scan_set_raises(self, tmp_path):
        repo, _ = _make_repo(tmp_path, {"docs/readme.md": "hi\n"})
        with pytest.raises(ScanError, match="no files"):
            problems(repo, {})

    def test_only_self_tracked_is_an_empty_scan_and_raises(self, tmp_path):
        repo, _ = _make_repo(tmp_path, {SELF: "X = 1\n"})
        with pytest.raises(ScanError, match="no files"):
            problems(repo, {})

    def test_unreadable_tracked_file_raises(self, tmp_path):
        repo, _ = _make_repo(tmp_path, {"tests/test_a.py": "X = 1\n", "tests/test_b.py": "Y = 1\n"})
        (repo / "tests/test_b.py").unlink()  # still tracked, no longer readable
        with pytest.raises(ScanError, match="cannot read"):
            problems(repo, {})

    def test_non_repo_raises(self, tmp_path):
        with pytest.raises(ScanError):
            problems(tmp_path, {})


# ---------------------------------------------------------------------------
# the real scan, over this repository
# ---------------------------------------------------------------------------


def test_every_full_length_hash_in_this_repos_tests_resolves():
    if is_shallow(REPO):
        pytest.skip("shallow clone: an object absent here proves nothing about the full history")
    msgs = problems(REPO, EXCEPTIONS)
    assert not msgs, "\n".join(msgs)
