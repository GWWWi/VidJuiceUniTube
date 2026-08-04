"""下载历史记录（SQLite 持久化）。

存放位置：``%APPDATA%/VidJuice/history.db``。

线程约定：连接使用 ``check_same_thread=False``，并由 ``threading.Lock``
串行化所有访问；业务上只在主线程（``DownloadManager`` 槽内）写入。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from src.core.exceptions import HistoryError
from src.core.models import DownloadTask
from src.utils.paths import history_db_path

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    resolution TEXT,
    ext TEXT,
    filepath TEXT,
    filesize INTEGER,
    status TEXT,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_title ON history(title);
CREATE INDEX IF NOT EXISTS idx_finished_at ON history(finished_at);
"""

_COLUMNS: tuple[str, ...] = (
    "id",
    "task_id",
    "url",
    "title",
    "resolution",
    "ext",
    "filepath",
    "filesize",
    "status",
    "finished_at",
)


class HistoryStore:
    """已完成下载任务的历史记录仓库。"""

    def __init__(self, path: str | None = None) -> None:
        """初始化历史记录仓库（不会立即建连，需调用 ``init_db()``）。

        Args:
            path: 数据库文件路径；``None`` 表示 ``%APPDATA%/VidJuice/history.db``。
        """
        self._path: str = path or history_db_path()
        self._conn: sqlite3.Connection | None = None
        self._lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    @property
    def path(self) -> str:
        """数据库文件绝对路径。"""
        return self._path

    def init_db(self) -> None:
        """建立连接并创建表结构（幂等）。

        Raises:
            HistoryError: 数据库无法打开或建表失败。
        """
        if self._conn is not None:
            return
        try:
            db_path = Path(self._path)
            if str(db_path) != ":memory:":
                db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
            self._conn = conn
            logger.info("历史数据库就绪：%s", self._path)
        except sqlite3.Error as exc:
            logger.error("历史数据库初始化失败：%s", exc)
            raise HistoryError("历史记录数据库初始化失败", detail=str(exc)) from exc

    def close(self) -> None:
        """关闭数据库连接（幂等）。"""
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error as exc:
                logger.warning("关闭历史数据库时出错：%s", exc)
            finally:
                self._conn = None
                logger.debug("历史数据库已关闭")

    def _require_conn(self) -> sqlite3.Connection:
        """返回可用连接；未初始化时自动初始化。"""
        if self._conn is None:
            self.init_db()
        if self._conn is None:  # pragma: no cover - init_db 失败会先抛异常
            raise HistoryError("历史记录数据库不可用")
        return self._conn

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def add(self, task: DownloadTask) -> int:
        """写入一条历史记录。

        Args:
            task: 已完成（或终态）的下载任务。

        Returns:
            新记录的自增 id；失败返回 ``-1``（不抛出，避免影响下载主流程）。
        """
        conn = self._require_conn()
        filesize = task.filesize
        if not filesize and task.progress and task.progress.total:
            filesize = int(task.progress.total)
        if not filesize and task.filepath:
            try:
                filesize = Path(task.filepath).stat().st_size
            except OSError:
                filesize = None

        row = (
            task.task_id,
            task.url,
            task.title or task.url,
            task.resolution or "",
            task.ext or "",
            task.filepath or "",
            int(filesize) if filesize else None,
            task.status.value,
            float(task.finished_at or time.time()),
        )
        try:
            with self._lock:
                cursor = conn.execute(
                    "INSERT INTO history"
                    "(task_id, url, title, resolution, ext, filepath, filesize, status, finished_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
                conn.commit()
                return int(cursor.lastrowid or -1)
        except sqlite3.Error as exc:
            logger.error("写入历史记录失败：%s", exc)
            return -1

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_all(self, limit: int = 500) -> list[dict[str, Any]]:
        """按完成时间倒序列出历史记录。

        Args:
            limit: 最大返回条数。

        Returns:
            记录字典列表；查询失败返回空列表。
        """
        conn = self._require_conn()
        try:
            with self._lock:
                cursor = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM history"
                    " ORDER BY finished_at DESC, id DESC LIMIT ?",
                    (int(limit),),
                )
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as exc:
            logger.error("查询历史记录失败：%s", exc)
            return []

    def search(self, keyword: str, limit: int = 500) -> list[dict[str, Any]]:
        """按关键词模糊搜索标题与 URL。

        Args:
            keyword: 关键词；为空时等价于 ``list_all()``。
            limit: 最大返回条数。

        Returns:
            记录字典列表。
        """
        key = str(keyword or "").strip()
        if not key:
            return self.list_all(limit)

        conn = self._require_conn()
        pattern = f"%{key}%"
        try:
            with self._lock:
                cursor = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM history"
                    " WHERE title LIKE ? OR url LIKE ?"
                    " ORDER BY finished_at DESC, id DESC LIMIT ?",
                    (pattern, pattern, int(limit)),
                )
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as exc:
            logger.error("搜索历史记录失败：%s", exc)
            return []

    def get(self, record_id: int) -> dict[str, Any] | None:
        """按 id 获取单条记录。

        Args:
            record_id: 记录 id。

        Returns:
            记录字典；不存在返回 ``None``。
        """
        conn = self._require_conn()
        try:
            with self._lock:
                cursor = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM history WHERE id = ?",
                    (int(record_id),),
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as exc:
            logger.error("读取历史记录失败：%s", exc)
            return None

    def count(self) -> int:
        """返回历史记录总条数。"""
        conn = self._require_conn()
        try:
            with self._lock:
                cursor = conn.execute("SELECT COUNT(*) AS c FROM history")
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except sqlite3.Error as exc:
            logger.error("统计历史记录失败：%s", exc)
            return 0

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------
    def delete(self, record_id: int) -> bool:
        """删除单条历史记录。

        Args:
            record_id: 记录 id。

        Returns:
            是否确实删除了一行。
        """
        conn = self._require_conn()
        try:
            with self._lock:
                cursor = conn.execute("DELETE FROM history WHERE id = ?", (int(record_id),))
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as exc:
            logger.error("删除历史记录失败：%s", exc)
            return False

    def clear(self) -> None:
        """清空全部历史记录。"""
        conn = self._require_conn()
        try:
            with self._lock:
                conn.execute("DELETE FROM history")
                conn.commit()
                logger.info("历史记录已清空")
        except sqlite3.Error as exc:
            logger.error("清空历史记录失败：%s", exc)


__all__ = ["HistoryStore"]
