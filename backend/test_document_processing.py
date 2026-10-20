import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from main import classify_document, parse_source_file


class DocumentClassificationTests(unittest.TestCase):
    def test_template_takes_priority(self):
        category, confidence, evidence = classify_document(
            "资料/项目申报模板.docx", "项目申报模板.docx", "技术改造项目申报模板"
        )
        self.assertEqual(category, "template")
        self.assertEqual(confidence, 0.99)
        self.assertTrue(evidence)

    def test_classifies_standard_from_folder_and_filename(self):
        category, confidence, evidence = classify_document(
            "汽机/相关标准规范/汽轮机检修规程.pdf", "汽轮机检修规程.pdf", ""
        )
        self.assertEqual(category, "standard")
        self.assertGreater(confidence, 0)
        self.assertIn("规范", evidence)

    def test_classifies_review_opinion(self):
        category, _, evidence = classify_document(
            "归档/专家评审意见.doc", "专家评审意见.doc", ""
        )
        self.assertEqual(category, "review_opinion")
        self.assertIn("评审意见", evidence)

    def test_unmatched_file_is_other(self):
        category, confidence, evidence = classify_document("资料/readme.txt", "readme.txt", "notes")
        self.assertEqual((category, confidence, evidence), ("other", 0.0, []))


class ParseRoutingTests(unittest.TestCase):
    @patch("main.ocr_image", return_value="图纸 OCR 文本")
    def test_image_routes_to_ocr(self, ocr):
        result = parse_source_file("汽机/系统图.png", b"image")
        self.assertEqual(result.status, "parsed")
        self.assertEqual(result.kind, "image")
        self.assertEqual(result.method, "ocr")
        ocr.assert_called_once_with("汽机/系统图.png", b"image")

    @patch("main.extract_docx", return_value="汽轮机改造项目")
    @patch("main.convert_doc_to_docx", return_value=(b"converted", "report.docx"))
    def test_doc_uses_libreoffice_conversion(self, convert, extract):
        result = parse_source_file("report.doc", b"legacy-doc")
        self.assertEqual(result.status, "parsed")
        self.assertEqual(result.kind, "doc")
        self.assertEqual(result.method, "libreoffice")
        self.assertEqual(result.converted_filename, "report.docx")
        convert.assert_called_once()
        extract.assert_called_once_with(b"converted")

    @patch("main.ocr_pdf", return_value="扫描件识别文本")
    @patch("main.extract_pdf", return_value=("[第1页] ", 1))
    def test_scanned_pdf_routes_to_ocr(self, extract, ocr):
        result = parse_source_file("scan.pdf", b"pdf")
        self.assertEqual(result.status, "parsed")
        self.assertEqual(result.method, "ocr")
        self.assertEqual(result.page_count, 1)
        ocr.assert_called_once_with(b"pdf", 1)

    @patch("main.ocr_pdf", return_value="")
    @patch("main.extract_pdf", return_value=("[第1页] ", 1))
    def test_unreadable_scanned_pdf_is_marked_for_follow_up(self, _extract, _ocr):
        result = parse_source_file("scan.pdf", b"pdf")
        self.assertEqual(result.status, "ocr_required")
        self.assertEqual(result.method, "ocr")


class MaterialPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = main.DB_PATH
        main.DB_PATH = Path(self.temp_dir.name) / "review.db"
        main.init_db()
        with main.db() as conn:
            conn.execute(
                "INSERT INTO projects(id,name,project_type,created_at) VALUES(?,?,?,?)",
                ("PRJ-TEST", "汽机资料包回归", "技术改造", main.utc_now()),
            )

    def tearDown(self):
        main.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def test_project_report_creates_a_candidate_package(self):
        with main.db() as conn:
            main.auto_assign_source_to_package(
                conn, "PRJ-TEST", 101, "project_report", "抗燃油系统改造可研报告.docx",
                "资料包/可研报告案例/抗燃油系统改造可研报告.docx",
                "项目名称：12号机组抗燃油系统改造\n保电热电厂 2026年\n",
            )
            package = conn.execute("SELECT * FROM material_packages").fetchone()
            assignment = conn.execute("SELECT * FROM project_documents WHERE source_id=101").fetchone()
        self.assertEqual(package["name"], "12号机组抗燃油系统改造")
        self.assertEqual(package["unit_no"], "12号机组")
        self.assertEqual(assignment["material_package_id"], package["id"])
        self.assertEqual(assignment["document_role"], "application")
        self.assertEqual(assignment["assignment_status"], "auto_assigned")

    def test_standard_stays_pending_for_manual_package_assignment(self):
        with main.db() as conn:
            main.auto_assign_source_to_package(
                conn, "PRJ-TEST", 102, "standard", "汽轮机检修规程.pdf",
                "资料包/相关标准规范/汽轮机检修规程.pdf", "",
            )
            assignment = conn.execute("SELECT * FROM project_documents WHERE source_id=102").fetchone()
        self.assertIsNone(assignment["material_package_id"])
        self.assertEqual(assignment["document_role"], "standard_basis")
        self.assertEqual(assignment["assignment_status"], "pending_assignment")


if __name__ == "__main__":
    unittest.main()
