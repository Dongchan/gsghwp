from __future__ import annotations

import csv
import os
import subprocess
import time
from io import StringIO
from pathlib import Path


def parse_hwp_process_ids(tasklist_output: str) -> frozenset[int]:
    process_ids: set[int] = set()
    for row in csv.reader(StringIO(tasklist_output)):
        if len(row) < 2 or row[0].casefold() != "hwp.exe":
            continue
        try:
            process_id = int(row[1].replace(",", ""), 10)
        except ValueError:
            continue
        if process_id > 0:
            process_ids.add(process_id)
    return frozenset(process_ids)


def capture_hwp_process_ids() -> frozenset[int]:
    completed = subprocess.run(
        [
            "tasklist.exe",
            "/FO",
            "CSV",
            "/NH",
        ],
        check=False,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        # 스캐너 실패는 "한/글이 하나도 없다"와 구별돼야 한다. 빈 집합을 돌려주면
        # 발견 루프가 살아 있는 한/글 전부를 떼었다 붙이며 리본을 전면 재구성한다
        # (프로세스 사망 사고의 재발 경로 — 적대 검증 exp5_flap 실측).
        detail = completed.stderr.strip()[:200]
        raise OSError(f"tasklist.exe exited {completed.returncode}: {detail}")
    return parse_hwp_process_ids(completed.stdout)


def new_process_ids(
    baseline: frozenset[int],
    current: frozenset[int],
) -> tuple[int, ...]:
    return tuple(sorted(current - baseline))


def wait_for_new_hwp_process_ids(
    baseline: frozenset[int],
    timeout_seconds: float,
) -> tuple[int, ...]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            current = capture_hwp_process_ids()
        except OSError:
            # 일시적 스캐너 실패는 마감까지 재시도한다.
            time.sleep(0.05)
            continue
        process_ids = new_process_ids(baseline, current)
        if process_ids:
            return process_ids
        time.sleep(0.05)
    return ()


def process_exists(process_id: int) -> bool:
    if process_id <= 0:
        return False
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True


def terminate_process_tree(process_id: int) -> bool:
    if not process_exists(process_id):
        return False
    completed = subprocess.run(
        ["taskkill", "/PID", str(process_id), "/T", "/F"],
        check=False,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.returncode == 0


def read_process_id(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, UnicodeError, ValueError):
        return None
    return value if value > 0 else None
