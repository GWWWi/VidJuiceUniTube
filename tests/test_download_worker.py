"""``DownloadWorker`` 的失败路径与产物校验测试。

这三个用例覆盖的是历史盲区——``download_worker.py`` 此前完全没有测试，
而工作区里最严重的两个缺陷都在这里：

1. 失败兜底访问 ``self._client._config``，客户端不带该属性时 ``AttributeError``
   会逃出 ``run()``，``task_failed`` 永不发出 → 任务永久停在「下载中」，
   并发名额泄漏，只能重启应用。
2. 合流格式（``needs_merge=False``）不做产物存在性校验，扩展名推断错误会把
   不存在的路径当成功上报，界面显示「已完成」而目录是空的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.models import DownloadTask
from src.workers.download_worker import DownloadWorker


class _FakeClient:
    """只实现 ``download`` 的最小替身。

    刻意**不提供** ``cookie_path`` / ``_config`` —— 这正是旧代码崩溃的场景。
    """

    def __init__(self, result: str | None = None, exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc

    def download(self, task: DownloadTask, hook, abort):  # type: ignore[no-untyped-def]
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeMerger:
    """本组用例不触发兜底合并，用空对象即可。"""


@pytest.fixture(name="task")
def task_fixture(tmp_path: Path) -> DownloadTask:
    """一个已就绪、无需合并的下载任务。"""
    return DownloadTask(
        url="https://example.com/watch?v=abc",
        title="示例视频",
        output_dir=str(tmp_path),
        needs_merge=False,
    )


def _run(worker: DownloadWorker) -> tuple[list[str], list[str], list[str]]:
    """执行 ``run()`` 并收集三个终态信号，返回 (finished, failed, statuses)。"""
    finished: list[str] = []
    failed: list[str] = []
    statuses: list[str] = []
    worker.signals.task_finished.connect(lambda _tid, path: finished.append(path))
    worker.signals.task_failed.connect(lambda _tid, msg: failed.append(msg))
    worker.signals.task_status_changed.connect(lambda _tid, st: statuses.append(st))
    worker.run()  # 不应抛任何异常
    return finished, failed, statuses


def test_failure_path_never_escapes_run(qt_app: object, task: DownloadTask) -> None:
    """下载异常必须转成 task_failed，而不是从 run() 逃逸。"""
    worker = DownloadWorker(
        task, _FakeClient(exc=RuntimeError("boom")), _FakeMerger()  # type: ignore[arg-type]
    )

    finished, failed, _statuses = _run(worker)

    assert finished == [], "失败时不应发出完成信号"
    assert len(failed) == 1, "必须发出且只发出一条 task_failed"
    assert failed[0], "失败消息不能为空"
    assert worker.is_finished(), "run() 必须正常收尾（否则并发名额泄漏）"


def test_missing_artifact_is_reported_as_failure(
    qt_app: object, task: DownloadTask
) -> None:
    """返回路径不存在时不能报成功。"""
    missing = str(Path(task.output_dir) / "not-there.mp4")
    worker = DownloadWorker(
        task, _FakeClient(result=missing), _FakeMerger()  # type: ignore[arg-type]
    )

    finished, failed, _statuses = _run(worker)

    assert finished == []
    assert len(failed) == 1
    assert "未找到产物" in failed[0]


def test_success_path_emits_real_file(qt_app: object, task: DownloadTask) -> None:
    """产物存在时才发出 task_finished，并带上真实路径。"""
    real = Path(task.output_dir) / "ok.mp4"
    real.write_bytes(b"data")
    worker = DownloadWorker(
        task, _FakeClient(result=str(real)), _FakeMerger()  # type: ignore[arg-type]
    )

    finished, failed, _statuses = _run(worker)

    assert failed == []
    assert finished == [str(real)]
