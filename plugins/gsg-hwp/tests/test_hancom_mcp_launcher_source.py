from pathlib import Path


_SOURCE = (
    Path(__file__).resolve().parents[1] / "addon" / "HancomMcpLauncher" / "Launcher.cpp"
).read_text(encoding="utf-8")


def test_launcher_failure_cleanup_has_no_unbounded_wait() -> None:
    assert "termination_wait_milliseconds = 5'000" in _SOURCE
    assert "TerminateProcess(child_process, operation_error) == FALSE" in _SOURCE
    assert _SOURCE.count("WaitForSingleObject(child_process.Get(), INFINITE)") == 1


def test_launcher_reports_desktop_token_failure_stage() -> None:
    assert "DuplicateTokenEx(explorer.exe)" in _SOURCE
    assert "desktop token acquisition failed at %ls" in _SOURCE
    assert "No child process was started." in _SOURCE


def test_launcher_duplicates_desktop_token_only_when_integrity_differs() -> None:
    finder_start = _SOURCE.index("bool FindDesktopProcess(")
    finder_end = _SOURCE.index("bool ResolveExecutable(", finder_start)
    finder = _SOURCE[finder_start:finder_end]
    integrity_branch = _SOURCE.index(
        "if (current_integrity_rid != desktop_integrity_rid)"
    )
    duplicate = _SOURCE.index("DuplicateTokenEx(", integrity_branch)
    branch = _SOURCE[integrity_branch:duplicate]

    assert "TOKEN_QUERY," in finder
    assert "TOKEN_DUPLICATE" not in finder
    assert "TOKEN_ASSIGN_PRIMARY" not in finder
    assert "DuplicateTokenEx(" not in finder
    assert "TOKEN_QUERY | TOKEN_DUPLICATE" in branch
    assert "OpenProcessToken(explorer.exe, duplicate)" in branch
    assert duplicate > integrity_branch


def test_launcher_distinguishes_the_process_creation_token() -> None:
    assert "CreateProcessW(current token)" in _SOURCE
    assert "CreateProcessWithTokenW(desktop token)" in _SOURCE
