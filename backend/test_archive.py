import io
import unittest
import zipfile
from unittest.mock import patch

from main import MAX_ARCHIVE_DEPTH, expand_upload


def zip_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class ArchiveExpansionTests(unittest.TestCase):
    def test_expands_each_file_and_preserves_nested_path(self):
        nested = zip_bytes({"inside.txt": "汽轮机 DEH".encode()})
        archive = zip_bytes({"reports/a.txt": "汽轮机".encode(), "nested.zip": nested})

        sources = expand_upload("package.zip", archive)

        self.assertEqual([item["source_path"] for item in sources], [
            "package.zip/reports/a.txt",
            "package.zip/nested.zip",
            "package.zip/nested.zip/inside.txt",
        ])
        self.assertEqual([item["archive_depth"] for item in sources], [1, 1, 2])
        self.assertEqual(sources[1]["kind"], "zip")

    def test_rejects_unsafe_archive_path(self):
        archive = zip_bytes({"../outside.txt": b"unsafe"})
        with self.assertRaisesRegex(ValueError, "不安全路径"):
            expand_upload("unsafe.zip", archive)

    def test_rejects_excessive_archive_depth(self):
        nested = zip_bytes({"leaf.txt": b"content"})
        for _ in range(MAX_ARCHIVE_DEPTH):
            nested = zip_bytes({"nested.zip": nested})
        with self.assertRaisesRegex(ValueError, "嵌套层级"):
            expand_upload("deep.zip", nested)

    def test_rejects_too_many_files(self):
        archive = zip_bytes({"a.txt": b"a", "b.txt": b"b"})
        with patch("main.MAX_ARCHIVE_FILES", 1), self.assertRaisesRegex(ValueError, "文件数量"):
            expand_upload("many.zip", archive)

    def test_rejects_declared_size_limits(self):
        archive = zip_bytes({"a.txt": b"123", "b.txt": b"456"})
        with patch("main.MAX_ARCHIVE_FILE_BYTES", 2), self.assertRaisesRegex(ValueError, "单个文件"):
            expand_upload("large-file.zip", archive)
        with patch("main.MAX_ARCHIVE_FILE_BYTES", 10), patch("main.MAX_ARCHIVE_EXPANDED_BYTES", 5), self.assertRaisesRegex(ValueError, "总大小"):
            expand_upload("large-total.zip", archive)


if __name__ == "__main__":
    unittest.main()
