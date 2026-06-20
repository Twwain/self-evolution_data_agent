"""Test extraction tools path safety + error contract."""
import tempfile
from pathlib import Path

import pytest

from app.knowledge.extraction_tools import find_files, grep, list_dir, read_file


@pytest.fixture
def temp_root():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "src" / "Foo.java").write_text("public class Foo {\n    private String name;\n}")
        (root / "pom.xml").write_text("<project></project>")
        yield str(root)


class TestPathSafety:
    def test_list_dir_absolute_rejected(self, temp_root):
        result = list_dir("/etc", temp_root)
        assert result["status"] == "error"
        assert result["error_type"] == "ACCESS_DENIED"

    def test_read_file_traversal_rejected(self, temp_root):
        result = read_file("../../../etc/passwd", temp_root)
        assert result["status"] == "error"

    def test_read_file_sibling_prefix_escape_rejected(self):
        # 兄弟目录前缀碰撞: root=<base>/repo, 兄弟=<base>/repo-evil 共享字符串前缀。
        # str.startswith 会误判 repo-evil 在沙箱内 — 必须用路径边界拦截。
        with tempfile.TemporaryDirectory() as base:
            root = Path(base) / "repo"
            root.mkdir()
            (root / "ok.txt").write_text("safe")
            evil = Path(base) / "repo-evil"
            evil.mkdir()
            (evil / "secret.txt").write_text("LEAKED")
            result = read_file("../repo-evil/secret.txt", str(root))
            assert result["status"] == "error"
            assert result["error_type"] == "ACCESS_DENIED"

    def test_read_file_ok(self, temp_root):
        result = read_file("pom.xml", temp_root)
        assert result["status"] == "ok"
        assert result["total_lines"] > 0


class TestGrepNeverSilent:
    def test_grep_no_match_returns_searched_files(self, temp_root):
        result = grep("NONEXISTENT", "src", temp_root)
        assert result["status"] == "ok"
        assert result["searched_files"] > 0
        assert result["matches"] == []

    def test_grep_dead_path_returns_error(self, temp_root):
        result = grep("foo", "ghost_dir", temp_root)
        assert result["status"] == "error"
        assert result["error_type"] == "PATH_NOT_FOUND"

    def test_grep_finds_match(self, temp_root):
        result = grep("String name", "src", temp_root)
        assert result["status"] == "ok"
        assert len(result["matches"]) > 0


class TestFindFiles:
    def test_find_files_by_ext(self, temp_root):
        result = find_files("*.java", temp_root)
        assert result["status"] == "ok"
        assert any("Foo.java" in f for f in result["files"])

    def test_find_files_glob_path(self, temp_root):
        result = find_files("**/*.xml", temp_root)
        assert result["status"] == "ok"
