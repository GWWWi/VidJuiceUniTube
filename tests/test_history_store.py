"""``src.core.history_store`` 单元测试。"""

from __future__ import annotations

import time
from pathlib import Path

from src.core.history_store import HistoryStore
from src.core.models import DownloadTask, MediaFormat, TaskStatus


def _make_task(title: str, url: str, output_dir: str, filesize: int | None = 1024) -> DownloadTask:
    """构造一条已完成的任务，供历史写入测试使用。

    Args:
        title: 视频标题。
        url: 视频链接。
        output_dir: 输出目录。
        filesize: 文件大小。

    Returns:
        终态为 ``COMPLETED`` 的任务。
    """
    fmt = MediaFormat(format_id="137", ext="mp4", height=1080, vcodec="avc1", acodec="none")
    task = DownloadTask.from_media(url=url, title=title, fmt=fmt, output_dir=output_dir)
    task.status = TaskStatus.COMPLETED
    task.filepath = str(Path(output_dir) / f"{title}.mp4")
    task.filesize = filesize
    task.finished_at = time.time()
    return task


class TestLifecycle:
    """生命周期测试。"""

    def test_init_db_is_idempotent(self, app_dir: Path) -> None:
        """重复 ``init_db`` 不报错。"""
        store = HistoryStore(str(app_dir / "h.db"))
        store.init_db()
        store.init_db()
        assert store.count() == 0
        store.close()

    def test_close_is_idempotent(self, app_dir: Path) -> None:
        """重复 ``close`` 不报错。"""
        store = HistoryStore(str(app_dir / "h.db"))
        store.init_db()
        store.close()
        store.close()

    def test_lazy_init_on_query(self, app_dir: Path) -> None:
        """未显式 ``init_db`` 时查询会自动建连。"""
        store = HistoryStore(str(app_dir / "lazy.db"))
        assert store.list_all() == []
        store.close()

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        """父目录不存在时自动创建。"""
        target = tmp_path / "deep" / "nested" / "history.db"
        store = HistoryStore(str(target))
        store.init_db()
        assert target.is_file()
        store.close()


class TestCrud:
    """增删查测试。"""

    def test_add_and_list(self, history: HistoryStore, download_dir: str) -> None:
        """写入后可查询。"""
        record_id = history.add(_make_task("视频A", "https://a.com/1", download_dir))
        assert record_id > 0

        rows = history.list_all()
        assert len(rows) == 1
        assert rows[0]["title"] == "视频A"
        assert rows[0]["status"] == TaskStatus.COMPLETED.value
        assert rows[0]["filesize"] == 1024

    def test_add_infers_filesize_from_file(self, history: HistoryStore, download_dir: str) -> None:
        """未知体积时从磁盘文件推断。"""
        task = _make_task("视频B", "https://a.com/2", download_dir, filesize=None)
        Path(task.filepath or "").write_bytes(b"x" * 333)

        history.add(task)
        assert history.list_all()[0]["filesize"] == 333

    def test_list_ordered_desc(self, history: HistoryStore, download_dir: str) -> None:
        """按完成时间倒序。"""
        old = _make_task("旧视频", "https://a.com/old", download_dir)
        old.finished_at = 1000.0
        new = _make_task("新视频", "https://a.com/new", download_dir)
        new.finished_at = 2000.0

        history.add(old)
        history.add(new)

        titles = [row["title"] for row in history.list_all()]
        assert titles == ["新视频", "旧视频"]

    def test_list_respects_limit(self, history: HistoryStore, download_dir: str) -> None:
        """limit 生效。"""
        for i in range(5):
            history.add(_make_task(f"视频{i}", f"https://a.com/{i}", download_dir))
        assert len(history.list_all(limit=3)) == 3

    def test_search_by_title_and_url(self, history: HistoryStore, download_dir: str) -> None:
        """标题与 URL 均可模糊匹配。"""
        history.add(_make_task("Python 教程", "https://youtube.com/p1", download_dir))
        history.add(_make_task("Rust 教程", "https://bilibili.com/r1", download_dir))

        assert len(history.search("Python")) == 1
        assert len(history.search("教程")) == 2
        assert len(history.search("bilibili")) == 1
        assert len(history.search("不存在")) == 0

    def test_search_empty_equals_list_all(self, history: HistoryStore, download_dir: str) -> None:
        """空关键词等价于列出全部。"""
        history.add(_make_task("视频", "https://a.com/1", download_dir))
        assert len(history.search("  ")) == len(history.list_all())

    def test_get(self, history: HistoryStore, download_dir: str) -> None:
        """按 id 精确查询。"""
        record_id = history.add(_make_task("视频C", "https://a.com/3", download_dir))
        assert (history.get(record_id) or {})["title"] == "视频C"
        assert history.get(999999) is None

    def test_count(self, history: HistoryStore, download_dir: str) -> None:
        """条数统计。"""
        assert history.count() == 0
        history.add(_make_task("视频D", "https://a.com/4", download_dir))
        assert history.count() == 1

    def test_delete(self, history: HistoryStore, download_dir: str) -> None:
        """删除单条。"""
        record_id = history.add(_make_task("视频E", "https://a.com/5", download_dir))
        assert history.delete(record_id) is True
        assert history.delete(record_id) is False
        assert history.count() == 0

    def test_clear(self, history: HistoryStore, download_dir: str) -> None:
        """清空全部。"""
        for i in range(3):
            history.add(_make_task(f"视频{i}", f"https://a.com/{i}", download_dir))
        history.clear()
        assert history.count() == 0

    def test_persists_across_reopen(self, app_dir: Path, download_dir: str) -> None:
        """关闭后重开仍能读到数据。"""
        path = str(app_dir / "persist.db")
        first = HistoryStore(path)
        first.init_db()
        first.add(_make_task("持久化视频", "https://a.com/persist", download_dir))
        first.close()

        second = HistoryStore(path)
        second.init_db()
        assert second.count() == 1
        second.close()
