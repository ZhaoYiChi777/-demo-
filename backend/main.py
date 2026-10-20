from __future__ import annotations

import hashlib
import csv
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
import zipfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from docx import Document
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pypdf import PdfReader
from dotenv import load_dotenv
from visual_assets import init_assets, collect_assets, assets_router

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / '.env')
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
CONVERTED_DIR = DATA_DIR / "converted"
LOG_DIR = DATA_DIR / "logs"
REPORT_DIR = DATA_DIR / "reports"
DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
CONVERTED_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)
KNOWLEDGE_DIR = ROOT / "csv" if (ROOT / "csv").is_dir() else ROOT.parent / "csv"

def read_knowledge_csv(filename: str, header_key: str) -> list[dict[str, str]]:
    """Read a knowledge CSV whose first rows are title/description preamble."""
    path = KNOWLEDGE_DIR / filename
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    header_index = next((i for i, row in enumerate(rows) if row and row[0].strip() == header_key), None)
    if header_index is None:
        return []
    headers = [cell.strip() for cell in rows[header_index]]
    result: list[dict[str, str]] = []
    for row in rows[header_index + 1:]:
        if not any(cell.strip() for cell in row):
            continue
        values = row + [""] * max(0, len(headers) - len(row))
        result.append({headers[i]: values[i].strip() for i in range(len(headers))})
    return result


def load_knowledge_rules() -> list[dict[str, str]]:
    return [row for row in read_knowledge_csv("汽机项目预评审完整知识库_v1__原子规则库.csv", "规则ID") if row.get("规则ID")]

KNOWLEDGE_RULES = load_knowledge_rules()
KNOWLEDGE_TECHNICAL_RULES = read_knowledge_csv("汽机项目预评审完整知识库_v1__专项技术规则.csv", "系统/专题")
KNOWLEDGE_CASES = read_knowledge_csv("汽机项目预评审完整知识库_v1__项目案例库.csv", "项目ID")
DB_PATH = DATA_DIR / "review.db"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILES = 300
MAX_ARCHIVE_FILE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 3
PDF_TEXT_THRESHOLD = 100
MAX_OCR_PAGES = 150
LLM_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("ALIYUN_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_MODEL = os.getenv("UNIFIED_MODEL", "deepseek-flash")
LLM_ENABLED = os.getenv("LLM_ENABLED", "true").lower() not in {"0", "false", "no"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_DIR / "review-api.log", encoding="utf-8")],
)
logger = logging.getLogger("review-api")

app = FastAPI(title="Project Review RAG API", version="0.1.0")

SPECIALTY_PROFILES = {
    "TURB": {
        "name": "汽机",
        "strong": ["给水泵汽轮机", "汽轮机", "EH油", "抗燃油", "DEH", "MEH", "高压旁路", "高旁", "TSI", "轴系保护"],
        "medium": ["汽机", "小机", "低旁", "抽汽", "汽封", "疏水", "伺服阀", "调速汽门", "凝结水泵", "冷油器", "空冷岛"],
        "weak": ["蒸汽", "机组", "阀门", "油站", "热网", "供热"],
    },
    "ELEC": {
        "name": "电气",
        "strong": ["发电机", "变压器", "励磁系统", "继电保护", "高压开关柜", "厂用电系统", "母线", "GIS"],
        "medium": ["励磁", "继保", "开关柜", "电缆", "电气一次", "电气二次", "同期", "蓄电池", "UPS", "直流系统", "短路电流"],
        "weak": ["电源", "控制柜", "模拟量", "硬接线", "电缆敷设"],
    },
    "BOIL": {
        "name": "锅炉",
        "strong": ["锅炉本体", "锅炉", "受热面", "燃烧器", "磨煤机", "制粉系统", "脱硝系统", "汽包"],
        "medium": ["制粉", "脱硝", "给煤", "风烟", "省煤器", "过热器", "再热器", "空气预热器", "烟气", "炉膛", "吹灰器"],
        "weak": ["燃烧", "煤粉", "烟气", "风机", "给水", "排烟"],
    },
}
# Backward-compatible flat dictionary for callers that only need terms.
SPECIALTY_TERMS = {
    code: profile["strong"] + profile["medium"] + profile["weak"]
    for code, profile in SPECIALTY_PROFILES.items()
}
DIMENSION_TERMS = {
    "建设必要性": ["必要性", "缺陷", "故障", "风险", "改造原因", "非计划停运"],
    "安全性": ["人身安全", "设备安全", "高压", "高温", "泄漏", "保护", "危险"],
    "可靠性": ["可靠性", "备用", "冗余", "老化", "超期", "失效", "可用率"],
    "经济性": ["投资", "费用", "效益", "回收期", "电量损失", "利润", "经济"],
    "实施条件": ["停机", "施工", "工期", "招标", "采购", "调试", "验收"],
}


class SpecialtyClassifyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)


class DocumentCategoryUpdateRequest(BaseModel):
    category: str = Field(min_length=1, max_length=40)


class MaterialPackageCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    unit_no: str | None = Field(default=None, max_length=40)
    plant_name: str | None = Field(default=None, max_length=120)
    year: str | None = Field(default=None, max_length=10)
    equipment_object: str | None = Field(default=None, max_length=160)


class SourceAssignmentRequest(BaseModel):
    material_package_id: int | None = None
    document_role: str | None = Field(default=None, max_length=40)


class ReviewIssueUpdateRequest(BaseModel):
    action: str = Field(default="pending", max_length=20)
    expert_level: str | None = Field(default=None, max_length=20)
    expert_opinion: str | None = Field(default=None, max_length=10_000)
    expert_note: str | None = Field(default=None, max_length=5_000)
    title: str | None = Field(default=None, max_length=300)
    suggestion: str | None = Field(default=None, max_length=5_000)


class ReviewConfirmRequest(BaseModel):
    expert_conclusion: str = Field(min_length=1, max_length=40)
    expert_note: str = Field(default="", max_length=10_000)


@dataclass
class ParseResult:
    text: str
    kind: str
    status: str
    error: str | None
    method: str = "none"
    converted_data: bytes | None = None
    converted_filename: str | None = None
    page_count: int | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, specialty TEXT, project_type TEXT,
          status TEXT NOT NULL DEFAULT 'uploaded', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS documents(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, filename TEXT NOT NULL,
          document_type TEXT NOT NULL, file_hash TEXT NOT NULL, content TEXT NOT NULL,
          parse_status TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS document_sources(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
          document_id INTEGER, source_path TEXT NOT NULL, source_archive TEXT,
          archive_depth INTEGER NOT NULL DEFAULT 0, filename TEXT NOT NULL,
          file_hash TEXT NOT NULL, file_size INTEGER NOT NULL, file_kind TEXT NOT NULL,
          parse_status TEXT NOT NULL, parse_error TEXT, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_documents_project_hash ON documents(project_id, file_hash);
        CREATE INDEX IF NOT EXISTS idx_document_sources_project ON document_sources(project_id, id);
        CREATE TABLE IF NOT EXISTS chunks(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, document_id INTEGER NOT NULL,
          chunk_type TEXT NOT NULL, section_path TEXT NOT NULL, text TEXT NOT NULL,
          page INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS findings(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, dimension TEXT NOT NULL,
          severity TEXT NOT NULL, title TEXT NOT NULL, judgement TEXT NOT NULL,
          suggestion TEXT NOT NULL, evidence_json TEXT NOT NULL, conclusion TEXT NOT NULL,
          material_package_id INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS specialty_assessments(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, document_id INTEGER,
          primary_specialty TEXT NOT NULL, related_specialties_json TEXT NOT NULL,
          confidence REAL NOT NULL, status TEXT NOT NULL, scores_json TEXT NOT NULL,
          evidence_json TEXT NOT NULL, method TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS material_packages(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, package_key TEXT NOT NULL,
          name TEXT NOT NULL, unit_no TEXT, plant_name TEXT, year TEXT, equipment_object TEXT,
          assignment_source TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(project_id, package_key));
        CREATE TABLE IF NOT EXISTS project_documents(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, material_package_id INTEGER,
          source_id INTEGER NOT NULL, document_role TEXT NOT NULL, assignment_status TEXT NOT NULL,
          assignment_source TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(project_id, source_id));
        CREATE TABLE IF NOT EXISTS review_sessions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
          material_package_id INTEGER, ai_conclusion TEXT NOT NULL,
          expert_conclusion TEXT, status TEXT NOT NULL DEFAULT 'expert_review',
          expert_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
          confirmed_at TEXT, archived_at TEXT, report_id INTEGER);
        CREATE INDEX IF NOT EXISTS idx_review_sessions_project ON review_sessions(project_id, id);
        CREATE TABLE IF NOT EXISTS review_issues(
          id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
          source_finding_id INTEGER, dimension TEXT NOT NULL, title TEXT NOT NULL,
          ai_level TEXT NOT NULL, expert_level TEXT NOT NULL, ai_opinion TEXT NOT NULL,
          expert_opinion TEXT NOT NULL, suggestion TEXT NOT NULL, evidence_json TEXT NOT NULL,
          action TEXT NOT NULL DEFAULT 'pending', expert_note TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL, UNIQUE(session_id, source_finding_id));
        CREATE INDEX IF NOT EXISTS idx_review_issues_session ON review_issues(session_id, id);
        CREATE TABLE IF NOT EXISTS review_action_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
          issue_id INTEGER, action TEXT NOT NULL, before_json TEXT NOT NULL,
          after_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reports(
          id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
          project_id TEXT NOT NULL, filename TEXT NOT NULL, path TEXT NOT NULL,
          created_at TEXT NOT NULL);
        """)
        ensure_column(conn, "document_sources", "document_category", "TEXT NOT NULL DEFAULT 'other'")
        ensure_column(conn, "document_sources", "category_confidence", "REAL")
        ensure_column(conn, "document_sources", "category_evidence", "TEXT")
        ensure_column(conn, "document_sources", "category_source", "TEXT NOT NULL DEFAULT 'rule'")
        ensure_column(conn, "document_sources", "parse_method", "TEXT NOT NULL DEFAULT 'none'")
        ensure_column(conn, "document_sources", "converted_path", "TEXT")
        ensure_column(conn, "document_sources", "page_count", "INTEGER")
        init_assets(conn)
        ensure_column(conn, "findings", "material_package_id", "INTEGER")
        conn.execute("UPDATE document_sources SET parse_method='direct' WHERE parse_status='parsed' AND parse_method='none'")
        backfill_source_categories(conn)
        backfill_material_package_assignments(conn)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def extract_docx(data: bytes) -> str:
    doc = Document(io.BytesIO(data))
    parts: list[str] = []
    for p in doc.paragraphs:
        text = clean_text(p.text)
        if text:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [clean_text(cell.text) for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def extract_pdf(data: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        pages.append(f"[第{i}页] {clean_text(page.extract_text() or '')}")
    return "\n".join(pages), len(reader.pages)


def extract_file(filename: str, data: bytes) -> tuple[str, str]:
    """Extract one leaf file; archive traversal is handled by expand_upload."""
    ext = Path(filename).suffix.lower()
    if ext == ".docx":
        return extract_docx(data), "docx"
    if ext == ".pdf":
        return extract_pdf(data)[0], "pdf"
    if ext == ".txt":
        return data.decode("utf-8", errors="ignore"), "text"
    if ext == ".zip":
        raise ValueError("压缩包需通过资料拆分流程处理")
    if ext == ".doc":
        raise ValueError("旧版 .doc 需要先转换为 .docx 或 PDF")
    raise ValueError(f"暂不支持文件类型: {ext or '无扩展名'}")


def safe_archive_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    pure_path = PurePosixPath(normalized)
    if pure_path.is_absolute() or not pure_path.parts or any(part in {"", ".", ".."} for part in pure_path.parts):
        raise ValueError(f"压缩包包含不安全路径: {path}")
    return str(pure_path)


def convert_doc_to_docx(filename: str, data: bytes) -> tuple[bytes, str]:
    with tempfile.TemporaryDirectory(prefix="review-doc-") as temp_dir:
        temp_path = Path(temp_dir)
        source = temp_path / f"source{Path(filename).suffix.lower()}"
        output_dir = temp_path / "output"
        output_dir.mkdir()
        source.write_bytes(data)
        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "docx", "--outdir", str(output_dir), str(source)],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        converted = output_dir / "source.docx"
        if result.returncode != 0 or not converted.exists():
            message = (result.stderr or result.stdout or "LibreOffice 未生成 DOCX").strip()
            raise ValueError(message[:500])
        return converted.read_bytes(), f"{Path(filename).stem}.docx"


def ocr_pdf(data: bytes, page_count: int) -> str:
    if page_count > MAX_OCR_PAGES:
        raise ValueError(f"PDF 页数超过 OCR 上限 {MAX_OCR_PAGES} 页")
    from local_ocr import read_pdf
    reader = PdfReader(io.BytesIO(data))
    return read_pdf(data, [clean_text(page.extract_text() or '') for page in reader.pages])


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def ocr_image(filename: str, data: bytes) -> str:
    from local_ocr import read_image
    return clean_text(read_image(data))


def parse_source_file(filename: str, data: bytes) -> ParseResult:
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".doc":
            converted_data, converted_filename = convert_doc_to_docx(filename, data)
            text = extract_docx(converted_data)
            if not text.strip():
                return ParseResult("", "doc", "empty_content", "转换后未解析出文本", "libreoffice", converted_data, converted_filename)
            return ParseResult(text, "doc", "parsed", None, "libreoffice", converted_data, converted_filename)
        if ext == ".pdf":
            text, page_count = extract_pdf(data)
            plain_text_size = len(re.sub(r"\[第\d+页\]", "", text).strip())
            # Check every page: a text cover must not hide scanned body pages.
            page_texts = re.split(r'\[第\d+页\]', text)[1:]
            if plain_text_size >= PDF_TEXT_THRESHOLD and all(len(p.strip()) >= PDF_TEXT_THRESHOLD for p in page_texts):
                return ParseResult(text, "pdf", "parsed", None, "direct", page_count=page_count)
            ocr_text = ocr_pdf(data, page_count)
            if '【此页未识别出文字，请核对原页图像】' in ocr_text:
                return ParseResult(ocr_text, 'pdf', 'ocr_required', '存在未识别文字的页面，请核对原页图像。', 'ocr', page_count=page_count)
            if ocr_text.strip():
                return ParseResult(ocr_text, "pdf", "parsed", None, "ocr", page_count=page_count)
            return ParseResult(text, "pdf", "ocr_required", "OCR 未识别出文本", "ocr", page_count=page_count)
        if ext in IMAGE_EXTENSIONS:
            text = ocr_image(filename, data)
            if text:
                return ParseResult(text, "image", "parsed", None, "ocr")
            return ParseResult("", "image", "ocr_required", "图片 OCR 未识别出文本", "ocr")
        text, kind = extract_file(filename, data)
    except ImportError:
        return ParseResult('', file_kind(filename), 'ocr_required', '本地OCR依赖未安装，请安装 backend/requirements.txt 后重试。', 'ocr')
    except ValueError as exc:
        status = "conversion_failed" if ext == ".doc" else "ocr_required" if ext in ({".pdf"} | IMAGE_EXTENSIONS) else "unsupported"
        method = "libreoffice" if ext == ".doc" else "ocr" if ext in ({".pdf"} | IMAGE_EXTENSIONS) else "none"
        return ParseResult("", file_kind(filename), status, str(exc), method)
    except subprocess.TimeoutExpired as exc:
        status = "conversion_failed" if ext == ".doc" else "ocr_required" if ext in ({".pdf"} | IMAGE_EXTENSIONS) else "parse_failed"
        method = "libreoffice" if ext == ".doc" else "ocr" if ext in ({".pdf"} | IMAGE_EXTENSIONS) else "none"
        return ParseResult("", file_kind(filename), status, f"处理超时: {exc.cmd}", method)
    except Exception as exc:
        logger.warning("file parse failed filename=%s error=%s", filename, exc)
        return ParseResult("", file_kind(filename), "parse_failed", str(exc), "none")
    if not text.strip():
        return ParseResult("", kind, "empty_content", "未解析出文本", "direct")
    return ParseResult(text, kind, "parsed", None, "direct")


def file_kind(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".") or "unknown"


DOCUMENT_CATEGORY_RULES: dict[str, tuple[str, ...]] = {
    "template": ("模板", "模版"),
    "review_opinion": ("预评审意见", "评审意见", "审查意见", "专家意见", "审查报告"),
    "standard": ("标准规范", "标准", "规范", "规程", "制度", "办法"),
    "drawing": ("图纸", "系统图", "接线图", "设备图", "流程图", "布置图"),
    "project_report": ("可行性研究报告", "可研报告", "项目申报", "技术改造", "新建系统", "改造项目"),
}


def classify_document(source_path: str, filename: str, text: str) -> tuple[str, float, list[str]]:
    """Classify a source using its path, filename, and early extracted text."""
    searchable = clean_text(f"{source_path} {filename} {text[:2000]}").lower()
    extension = Path(filename).suffix.lower()
    scores: dict[str, int] = {}
    evidence: dict[str, list[str]] = {}
    for category, terms in DOCUMENT_CATEGORY_RULES.items():
        hits = [term for term in terms if term.lower() in searchable]
        if hits:
            scores[category] = len(hits)
            evidence[category] = hits
    if extension in {".dwg", ".dxf"}:
        scores["drawing"] = scores.get("drawing", 0) + 4
        evidence.setdefault("drawing", []).append(extension)
    if "模板" in searchable or "模版" in searchable:
        return "template", 0.99, evidence.get("template", ["模板"])
    if not scores:
        return "other", 0.0, []
    category, score = max(scores.items(), key=lambda item: (item[1], item[0] == "project_report"))
    confidence = round(score / sum(scores.values()), 3)
    return category, confidence, evidence[category]


def persist_conversion(project_id: str, source_hash: str, result: ParseResult) -> str | None:
    if not result.converted_data or not result.converted_filename:
        return None
    conversion_dir = CONVERTED_DIR / project_id
    conversion_dir.mkdir(parents=True, exist_ok=True)
    target = conversion_dir / f"{source_hash[:16]}_{Path(result.converted_filename).name}"
    if not target.exists():
        target.write_bytes(result.converted_data)
    return str(target.relative_to(DATA_DIR))


def backfill_source_categories(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """SELECT s.id,s.source_path,s.filename,d.content
             FROM document_sources s LEFT JOIN documents d ON d.id=s.document_id
            WHERE s.category_confidence IS NULL"""
    ).fetchall()
    for row in rows:
        category, confidence, evidence = classify_document(row["source_path"], row["filename"], row["content"] or "")
        conn.execute(
            "UPDATE document_sources SET document_category=?,category_confidence=?,category_evidence=?,category_source='rule' WHERE id=?",
            (category, confidence, json.dumps(evidence, ensure_ascii=False), row["id"]),
        )


SPECIALTY_ELIGIBLE_CATEGORIES = {"project_report", "review_opinion", "drawing"}
DOCUMENT_CATEGORIES = set(DOCUMENT_CATEGORY_RULES) | {"other"}
DOCUMENT_ROLES = {
    "application": "申报材料",
    "historical_review": "历史评审意见",
    "drawing": "图纸",
    "standard_basis": "标准依据",
    "template": "模板",
    "attachment": "其他附件",
}
CATEGORY_DOCUMENT_ROLES = {
    "project_report": "application",
    "review_opinion": "historical_review",
    "drawing": "drawing",
    "standard": "standard_basis",
    "template": "template",
    "other": "attachment",
}


def normalize_package_key(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value).lower()[:180] or "未命名资料包"


def extract_project_identity(filename: str, source_path: str, text: str) -> dict[str, str | None]:
    """Extract conservative project markers for candidate material-package grouping."""
    preview = f"{filename}\n{source_path}\n{text[:8000]}"
    name_match = re.search(r"(?:项目名称|工程名称)\s*[:：]\s*([^\n]{4,120})", preview)
    name = clean_text(name_match.group(1)) if name_match else ""
    if not name:
        stem = Path(filename).stem
        stem = re.sub(r"[（(][^）)]*(?:报审|终稿|送审|修改|模板)[^）)]*[）)]", "", stem)
        name = clean_text(stem)
    unit_match = re.search(r"(\d{1,2}(?:[、,，]\d{1,2})?号机组)", preview)
    plant_match = re.search(r"([^\s，。；;、]{2,40}(?:热电厂|发电厂|电厂))", preview)
    year_match = re.search(r"(20\d{2})年?", preview)
    equipment_terms = ("抗燃油系统", "给水泵", "凝结水泵", "空冷岛", "汽轮机", "变压器", "励磁系统", "锅炉", "脱硝系统")
    equipment = next((term for term in equipment_terms if term in preview), None)
    return {
        "name": name[:160] or "未命名项目资料包",
        "unit_no": unit_match.group(1) if unit_match else None,
        "plant_name": clean_text(plant_match.group(1)) if plant_match else None,
        "year": year_match.group(1) if year_match else None,
        "equipment_object": equipment,
    }


def upsert_material_package(conn: sqlite3.Connection, project_id: str, identity: dict[str, str | None], source: str) -> int:
    package_key = normalize_package_key(f"{identity['name']}|{identity['unit_no'] or ''}")
    existing = conn.execute(
        "SELECT id FROM material_packages WHERE project_id=? AND package_key=?", (project_id, package_key)
    ).fetchone()
    now = utc_now()
    if existing:
        conn.execute(
            """UPDATE material_packages SET unit_no=COALESCE(unit_no,?),plant_name=COALESCE(plant_name,?),
               year=COALESCE(year,?),equipment_object=COALESCE(equipment_object,?),updated_at=? WHERE id=?""",
            (identity["unit_no"], identity["plant_name"], identity["year"], identity["equipment_object"], now, existing["id"]),
        )
        return existing["id"]
    cur = conn.execute(
        """INSERT INTO material_packages(project_id,package_key,name,unit_no,plant_name,year,equipment_object,
           assignment_source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (project_id, package_key, identity["name"], identity["unit_no"], identity["plant_name"], identity["year"],
         identity["equipment_object"], source, now, now),
    )
    return cur.lastrowid


def auto_assign_source_to_package(
    conn: sqlite3.Connection, project_id: str, source_id: int, category: str, filename: str, source_path: str, text: str,
    package_id: int | None = None,
) -> None:
    role = CATEGORY_DOCUMENT_ROLES.get(category, "attachment")
    manual = package_id is not None
    assignment_status = "manual_assigned" if manual else "pending_assignment"
    if not manual and category == "project_report":
        package_id = upsert_material_package(conn, project_id, extract_project_identity(filename, source_path, text), "rule")
        assignment_status = "auto_assigned"
    elif not manual and category in {"review_opinion", "drawing"}:
        identity = extract_project_identity(filename, source_path, text)
        key = normalize_package_key(f"{identity['name']}|{identity['unit_no'] or ''}")
        match = conn.execute(
            "SELECT id FROM material_packages WHERE project_id=? AND package_key=?", (project_id, key)
        ).fetchone()
        if match:
            package_id = match["id"]
            assignment_status = "auto_assigned"
    now = utc_now()
    conn.execute(
        """INSERT INTO project_documents(project_id,material_package_id,source_id,document_role,assignment_status,
           assignment_source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(project_id,source_id) DO NOTHING""",
        (project_id, package_id, source_id, role, assignment_status, "manual" if manual else "rule", now, now),
    )


def backfill_material_package_assignments(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """SELECT s.id,s.project_id,s.document_category,s.filename,s.source_path,d.content
             FROM document_sources s LEFT JOIN documents d ON d.id=s.document_id
            WHERE NOT EXISTS (SELECT 1 FROM project_documents pd WHERE pd.project_id=s.project_id AND pd.source_id=s.id)"""
    ).fetchall()
    for row in rows:
        auto_assign_source_to_package(
            conn, row["project_id"], row["id"], row["document_category"], row["filename"], row["source_path"], row["content"] or "",
        )


def expand_upload(filename: str, data: bytes) -> list[dict[str, Any]]:
    """Expand an upload into independently traceable source files."""
    root_name = Path(filename).name or "upload.bin"
    if Path(root_name).suffix.lower() != ".zip":
        return [{
            "source_path": root_name, "source_archive": None, "archive_depth": 0,
            "filename": root_name, "data": data, "kind": file_kind(root_name),
        }]

    files: list[dict[str, Any]] = []
    totals = {"files": 0, "bytes": 0}

    def walk(archive_data: bytes, parent_path: str, depth: int) -> None:
        if depth > MAX_ARCHIVE_DEPTH:
            raise ValueError(f"压缩包嵌套层级不能超过 {MAX_ARCHIVE_DEPTH} 层")
        try:
            archive = zipfile.ZipFile(io.BytesIO(archive_data), metadata_encoding="gbk")
        except zipfile.BadZipFile as exc:
            raise ValueError(f"无效压缩包: {parent_path}") from exc
        with archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative_path = safe_archive_path(info.filename)
                totals["files"] += 1
                totals["bytes"] += info.file_size
                if totals["files"] > MAX_ARCHIVE_FILES:
                    raise ValueError(f"压缩包文件数量不能超过 {MAX_ARCHIVE_FILES}")
                if info.file_size > MAX_ARCHIVE_FILE_BYTES:
                    raise ValueError("压缩包内单个文件不能超过50MB")
                if totals["bytes"] > MAX_ARCHIVE_EXPANDED_BYTES:
                    raise ValueError("压缩包解压后的总大小不能超过 200MB")
                child_data = archive.read(info)
                child_path = f"{parent_path}/{relative_path}"
                if Path(relative_path).suffix.lower() == ".zip":
                    files.append({
                        "source_path": child_path, "source_archive": root_name, "archive_depth": depth,
                        "filename": Path(relative_path).name, "data": child_data, "kind": "zip",
                    })
                    walk(child_data, child_path, depth + 1)
                    continue
                files.append({
                    "source_path": child_path, "source_archive": root_name, "archive_depth": depth,
                    "filename": Path(relative_path).name, "data": child_data, "kind": file_kind(relative_path),
                })

    walk(data, root_name, 1)
    return files


def infer_specialty(text: str) -> tuple[str, dict[str, int], dict[str, Any]]:
    """Score specialties using weighted terms and return explainable evidence."""
    normalized = clean_text(text).lower()
    source_lower = text.lower()
    scores: dict[str, int] = {}
    details: dict[str, Any] = {}
    for code, profile in SPECIALTY_PROFILES.items():
        hits: list[dict[str, Any]] = []
        score = 0
        for tier, weight in (("strong", 8), ("medium", 4), ("weak", 1)):
            for term in profile[tier]:
                count = normalized.count(term.lower())
                if count:
                    score += count * weight
                    start = source_lower.find(term.lower())
                    excerpt = text[max(0, start - 45): min(len(text), start + len(term) + 90)]
                    hits.append({"term": term, "tier": tier, "count": count, "weight": weight, "excerpt": clean_text(excerpt)})
        scores[code] = score
        details[code] = {"name": profile["name"], "score": score, "hits": sorted(hits, key=lambda x: (-x["weight"], -x["count"]))[:8]}
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    primary, top_score = ranked[0]
    second_score = ranked[1][1]
    confidence = round(top_score / max(1, top_score + second_score + 4), 3)
    related = [code for code, score in ranked[1:] if score > 0 and score >= max(8, top_score * 0.28)]
    status = "unknown" if top_score == 0 else "auto_confirmed" if confidence >= 0.72 and not related else "needs_review"
    result = {"primary": primary if top_score else "UNKNOWN", "confidence": confidence if top_score else 0.0, "related": related, "status": status, "scores": scores, "details": details, "method": "weighted_dictionary_v1"}
    return result["primary"], scores, result


def save_specialty_assessment(
    conn: sqlite3.Connection,
    project_id: str,
    document_id: int,
    result: dict[str, Any],
) -> None:
    conn.execute(
        """INSERT INTO specialty_assessments(
             project_id,document_id,primary_specialty,related_specialties_json,
             confidence,status,scores_json,evidence_json,method,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            project_id,
            document_id,
            result["primary"],
            json.dumps(result["related"], ensure_ascii=False),
            result["confidence"],
            result["status"],
            json.dumps(result["scores"], ensure_ascii=False),
            json.dumps(result["details"], ensure_ascii=False),
            result["method"],
            utc_now(),
        ),
    )


def split_chunks(text: str, max_chars: int = 700) -> list[tuple[str, str, int | None]]:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    chunks: list[tuple[str, str, int | None]] = []
    section = "未分类"
    buf: list[str] = []
    page: int | None = None
    for line in lines:
        page_match = re.match(r"\[第(\d+)页\]", line)
        if page_match:
            page = int(page_match.group(1))
            line = line[page_match.end():].strip()
        if re.match(r"^(一|二|三|四|五|六|七|八|九|十)[、.．]", line) or re.match(r"^\d+[、.]", line):
            if buf:
                chunks.append((section, " ".join(buf), page))
                buf = []
            section = line[:100]
        buf.append(line)
        if sum(len(x) for x in buf) >= max_chars:
            chunks.append((section, " ".join(buf), page))
            buf = []
    if buf:
        chunks.append((section, " ".join(buf), page))
    return chunks


def keyword_score(query: str, text: str) -> float:
    terms = {t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_-]{2,}", query.lower())}
    if not terms:
        return 0.0
    lower = text.lower()
    hits = sum(lower.count(term) for term in terms)
    return min(1.0, hits / max(3, len(terms)))


def retrieve(project_id: str, query: str, limit: int = 5, material_package_id: int | None = None) -> list[dict[str, Any]]:
    package_clause = ""
    params: list[Any] = [project_id]
    if material_package_id is not None:
        package_clause = " AND EXISTS (SELECT 1 FROM project_documents pd WHERE pd.project_id=c.project_id AND pd.source_id=s.id AND pd.material_package_id=?)"
        params.append(material_package_id)
    with db() as conn:
        rows = conn.execute(
            """SELECT c.*, d.filename FROM chunks c JOIN documents d ON d.id=c.document_id
               WHERE c.project_id=? AND EXISTS (
                 SELECT 1 FROM document_sources s
                  WHERE s.project_id=c.project_id AND s.document_id=c.document_id
                    AND s.document_category IN ('project_report','review_opinion','drawing')
               """ + package_clause + ")",
            params,
        ).fetchall()
    ranked = []
    for row in rows:
        score = keyword_score(query, row["text"])
        if score > 0:
            ranked.append({"chunk_id": row["id"], "score": round(score, 3), "text": row["text"], "section": row["section_path"], "page": row["page"], "source_file": row["filename"]})
    return sorted(ranked, key=lambda x: x["score"], reverse=True)[:limit]


def project_text(project_id: str, material_package_id: int | None = None) -> str:
    package_clause = ""
    params: list[Any] = [project_id]
    if material_package_id is not None:
        package_clause = " AND EXISTS (SELECT 1 FROM document_sources s JOIN project_documents pd ON pd.project_id=s.project_id AND pd.source_id=s.id WHERE s.project_id=c.project_id AND s.document_id=c.document_id AND pd.material_package_id=?)"
        params.append(material_package_id)
    with db() as conn:
        rows = conn.execute(
            """SELECT c.text FROM chunks c
                WHERE c.project_id=? AND EXISTS (
                  SELECT 1 FROM document_sources s
                   WHERE s.project_id=c.project_id AND s.document_id=c.document_id
                     AND s.document_category IN ('project_report','review_opinion','drawing')
                )""" + package_clause,
            params,
        ).fetchall()
    return " ".join(row["text"] for row in rows)


def extract_response_text(payload: dict[str, Any]) -> str:
    """Read text from OpenAI Responses-compatible and Chat Completions payloads."""
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    parts: list[str] = []
    for output in payload.get("output", []) or []:
        for content in output.get("content", []) or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    if parts:
        return "\n".join(parts)
    for choice in payload.get("choices", []) or []:
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        if isinstance(message.get("content"), str):
            parts.append(message["content"])
    return "\n".join(parts)


def llm_review_synthesis(project: dict[str, Any], source_text: str, findings: list[dict[str, Any]], conclusion: str) -> tuple[list[dict[str, Any]], str] | None:
    """Ask the configured OpenAI-compatible model to summarize local rule findings.

    Retrieval and evidence stay local; the model only rewrites judgements, suggestions,
    and the four-value conclusion. A failure intentionally returns None for rule fallback.
    """
    if not (LLM_ENABLED and LLM_API_KEY and source_text.strip() and findings):
        return None
    prompt = {
        "project": {"name": project.get("name"), "specialty": project.get("specialty"), "project_type": project.get("project_type")},
        "local_findings": findings,
        "local_conclusion": conclusion,
        "material_excerpt": source_text[:18000],
    }
    instruction = (
        "你是发电企业项目预评审专家。保留输入问题的证据依据，不要编造标准条款或数值。"
        "根据本地规则已命中的问题，归纳为最多6条清晰的专家问题，并给出判断、修改建议和四类结论。"
        "只返回JSON，不要Markdown：{\"findings\":[{\"dimension\":\"安全性/可靠性/经济性/实施条件\",\"severity\":\"high/medium/low\",\"title\":\"...\",\"judgement\":\"...\",\"suggestion\":\"...\",\"evidence\":[] }],\"conclusion\":\"一致通过/少量修改后通过/大量修改后通过/不通过\"}"
    )
    try:
        result = model_gateway.json(instruction, prompt)
        generated = result.get("findings")
        if not isinstance(generated, list):
            return None
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(generated[:6]):
            if not isinstance(item, dict) or not item.get("title"):
                continue
            local = findings[index] if index < len(findings) else {}
            normalized.append({
                "dimension": item.get("dimension") or local.get("dimension", "可靠性"),
                "severity": item.get("severity") if item.get("severity") in {"high", "medium", "low"} else local.get("severity", "medium"),
                "title": str(item["title"])[:180], "judgement": str(item.get("judgement") or local.get("judgement", ""))[:800],
                "suggestion": str(item.get("suggestion") or local.get("suggestion", ""))[:800],
                "evidence": local.get("evidence", item.get("evidence", [])),
            })
        allowed = {"一致通过", "少量修改后通过", "大量修改后通过", "不通过"}
        final_conclusion = result.get("conclusion") if result.get("conclusion") in allowed else conclusion
        return (normalized or findings, final_conclusion)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, RuntimeError) as exc:
        logger.warning("llm synthesis unavailable model=%s error=%s", model_config()['model'], exc)
        return None


def build_findings(project_id: str, material_package_id: int | None = None) -> tuple[list[dict[str, Any]], str]:
    text = project_text(project_id, material_package_id)
    findings = []
    # Knowledge-base rules are used as evidence-aware checks; retain their IDs in logs/reports.
    kb_matches = [r for r in KNOWLEDGE_RULES if any(term in text for term in re.findall(r"[\u4e00-\u9fff]{2,8}", r.get("触发条件（自然语言）", ""))[:12])]
    checks = [
        ("经济性", "medium", "方案缺少定量投资比较", ["要求定量", "投资费用", "投资回报", "综合比较"], "补充备选方案的投资、停机工期、预期收益和回收期比较。"),
        ("可靠性", "high", "关键系统配置与备用能力需要进一步证明", ["缺少备用", "只有1套", "共用主机", "独立的高压油源"], "补充现状配置、失效后果、备用策略和改造后可靠性指标。"),
        ("实施条件", "medium", "停机窗口和切换风险说明不充分", ["需结合", "停机", "施工周期", "调试"], "明确12、13号机组停机窗口、系统切换步骤、回退方案和试运行安排。"),
    ]
    for dimension, severity, title, terms, suggestion in checks:
        query = " ".join(terms)
        evidence = retrieve(project_id, query, material_package_id=material_package_id)
        if not evidence:
            continue
        matched = any(term in text for term in terms)
        if not matched:
            continue
        findings.append({"dimension": dimension, "severity": severity, "title": title, "judgement": evidence[0]["text"][:260], "suggestion": suggestion, "evidence": evidence[:2]})
    logger.info("knowledge_rules_loaded=%s knowledge_rule_matches=%s project_id=%s", len(KNOWLEDGE_RULES), len(kb_matches), project_id)
    high_count = sum(f["severity"] == "high" for f in findings)
    conclusion = "不通过" if high_count >= 2 else "大量修改后通过" if len(findings) >= 4 else "少量修改后通过" if findings else "一致通过"
    with db() as conn:
        project_row = conn.execute("SELECT id,name,specialty,project_type FROM projects WHERE id=?", (project_id,)).fetchone()
    if project_row and findings:
        synthesized = llm_review_synthesis(dict(project_row), text, findings, conclusion)
        if synthesized:
            logger.info("llm synthesis applied project_id=%s model=%s findings=%s", project_id, model_config()['model'], len(synthesized[0]))
            return synthesized
    return findings, conclusion


@app.on_event("startup")
def startup() -> None:
    init_db()
    workbench.init_tables()
    review_agent.init_tables()
    batch_reviews.init_tables()
    accounts.init_tables()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "review-rag"}


@app.get("/api/specialties")
def list_specialties() -> dict[str, Any]:
    """Return the active specialty profiles for UI labels and audit display."""
    return {
        "specialties": [
            {"code": code, "name": profile["name"], "term_counts": {tier: len(profile[tier]) for tier in ("strong", "medium", "weak")}}
            for code, profile in SPECIALTY_PROFILES.items()
        ],
        "method": "weighted_dictionary_v1",
    }


@app.get("/api/knowledge-base")
def knowledge_base() -> dict[str, Any]:
    """Return the fixed demo knowledge base used by the rules and cases view."""
    rules = [
        {
            "id": row.get("规则ID", ""), "specialty": row.get("专业", ""), "category": row.get("类别", ""),
            "chapter": row.get("评审章节", ""), "name": row.get("规则名称", ""),
            "applicable_type": row.get("适用项目类型", ""), "system": row.get("适用系统/技术方向", ""),
            "check_object": row.get("检查对象", ""), "trigger": row.get("触发条件（自然语言）", ""),
            "evidence": row.get("所需证据字段", ""), "issue_level": row.get("问题级别", ""),
            "nature": row.get("规则性质", ""), "impact": row.get("总体结论影响", ""),
            "hard_soft": row.get("硬/软规则", ""), "opinion": row.get("标准评审意见", ""),
            "remedy": row.get("整改/补充要求", ""), "source_case": row.get("来源案例", ""),
            "source_type": row.get("来源类型", ""), "confidence": row.get("置信度", ""),
        }
        for row in KNOWLEDGE_RULES
    ]
    technical_rules = [
        {
            "topic": row.get("系统/专题", ""), "case_project": row.get("案例项目", ""),
            "content": row.get("典型改造内容", ""), "check_points": row.get("必须核验的技术点", ""),
            "acceptance": row.get("建议验收指标", ""), "core_rules": row.get("核心规则", ""),
            "note": row.get("备注", ""),
        }
        for row in KNOWLEDGE_TECHNICAL_RULES
    ]
    cases = [
        {
            "project_id": row.get("项目ID", ""), "plant": row.get("单位/电厂", ""),
            "project_name": row.get("项目名称", ""), "conclusion": row.get("初判结论", ""),
            "basis": row.get("初判依据", ""), "required": row.get("必须修改/补充事项", ""),
            "max_level": row.get("最高章节问题级别", ""), "priority": row.get("复核优先级", ""),
            "historical_opinion": row.get("已有历史评审意见", ""),
            "representative_rules": row.get("代表命中规则", ""), "purpose": row.get("案例用途", ""),
            "note": row.get("备注", ""),
        }
        for row in KNOWLEDGE_CASES
    ]
    return {"version": "汽机项目预评审完整知识库_v1", "fixed": True, "rules": rules, "technical_rules": technical_rules, "cases": cases}


@app.post("/api/specialty/classify")
def classify_specialty(payload: SpecialtyClassifyRequest) -> dict[str, Any]:
    """Classify free text without creating a project (dictionary/rule smoke test)."""
    _, _, result = infer_specialty(payload.text)
    return result


@app.get("/api/projects")
def list_projects() -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute("SELECT id,name,specialty,project_type,status,created_at FROM projects ORDER BY created_at DESC").fetchall()
    return {"projects": accounts.visible([dict(row) for row in rows])}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    with db() as conn:
        project = conn.execute("SELECT id,name,specialty,project_type,status,created_at FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            raise HTTPException(404, "项目不存在")
        docs = conn.execute("SELECT id,filename,document_type,file_hash,parse_status,created_at FROM documents WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        sources = conn.execute(
            """SELECT s.id,s.document_id,s.source_path,s.source_archive,s.archive_depth,
                      s.filename,s.file_hash,s.file_size,s.file_kind,s.document_category,
                      s.category_confidence,s.category_evidence,s.category_source,s.parse_status,s.parse_error,
                      s.parse_method,s.converted_path,s.page_count,s.created_at,
                      s.asset_scan_status,s.asset_scan_error,s.asset_count,pd.material_package_id,
                      pd.document_role,pd.assignment_status,pd.assignment_source
                 FROM document_sources s LEFT JOIN project_documents pd ON pd.project_id=s.project_id AND pd.source_id=s.id
                 WHERE s.project_id=? ORDER BY s.id""",
            (project_id,),
        ).fetchall()
        chunk_count = conn.execute("SELECT COUNT(*) AS count FROM chunks WHERE project_id=?", (project_id,)).fetchone()["count"]
        assessment = conn.execute("SELECT * FROM specialty_assessments WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()
    assessment_data = None
    if assessment:
        assessment_data = {
            "primary": assessment["primary_specialty"],
            "related": json.loads(assessment["related_specialties_json"]),
            "confidence": assessment["confidence"],
            "status": assessment["status"],
            "scores": json.loads(assessment["scores_json"]),
            "details": json.loads(assessment["evidence_json"]),
            "method": assessment["method"],
            "created_at": assessment["created_at"],
        }
    return {
        "project": dict(project), "documents": [dict(row) for row in docs],
        "source_files": [dict(row) for row in sources], "chunk_count": chunk_count,
        "specialty_assessment": assessment_data,
    }


@app.get("/api/projects/{project_id}/sources")
def list_project_sources(project_id: str) -> dict[str, Any]:
    with db() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        rows = conn.execute(
            """SELECT s.id,s.document_id,s.source_path,s.source_archive,s.archive_depth,
                      s.filename,s.file_hash,s.file_size,s.file_kind,s.document_category,
                      s.category_confidence,s.category_evidence,s.category_source,s.parse_status,s.parse_error,
                      s.parse_method,s.converted_path,s.page_count,s.created_at,
                      s.asset_scan_status,s.asset_scan_error,s.asset_count,pd.material_package_id,
                      pd.document_role,pd.assignment_status,pd.assignment_source
                 FROM document_sources s LEFT JOIN project_documents pd ON pd.project_id=s.project_id AND pd.source_id=s.id
                 WHERE s.project_id=? ORDER BY s.id""",
            (project_id,),
        ).fetchall()
    return {"project_id": project_id, "source_files": [dict(row) for row in rows]}


@app.get("/api/projects/{project_id}/material-packages")
def list_material_packages(project_id: str) -> dict[str, Any]:
    with db() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        packages = conn.execute(
            """SELECT mp.*,COUNT(pd.id) AS document_count,
                      SUM(CASE WHEN pd.assignment_status='pending_assignment' THEN 1 ELSE 0 END) AS pending_count
                 FROM material_packages mp LEFT JOIN project_documents pd ON pd.material_package_id=mp.id
                WHERE mp.project_id=? GROUP BY mp.id ORDER BY mp.updated_at DESC,mp.id DESC""",
            (project_id,),
        ).fetchall()
        pending = conn.execute(
            "SELECT COUNT(*) AS count FROM project_documents WHERE project_id=? AND assignment_status='pending_assignment'",
            (project_id,),
        ).fetchone()["count"]
    return {"project_id": project_id, "material_packages": [dict(row) for row in packages], "pending_assignment_count": pending}


@app.post("/api/projects/{project_id}/material-packages")
def create_material_package(project_id: str, payload: MaterialPackageCreateRequest) -> dict[str, Any]:
    identity = payload.model_dump()
    identity["name"] = clean_text(identity["name"])
    with db() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        package_id = upsert_material_package(conn, project_id, identity, "manual")
        package = conn.execute("SELECT * FROM material_packages WHERE id=?", (package_id,)).fetchone()
    return dict(package)


@app.patch("/api/projects/{project_id}/sources/{source_id}/assignment")
def assign_source_to_material_package(project_id: str, source_id: int, payload: SourceAssignmentRequest) -> dict[str, Any]:
    with db() as conn:
        source = conn.execute(
            "SELECT id,document_category FROM document_sources WHERE id=? AND project_id=?", (source_id, project_id)
        ).fetchone()
        if not source:
            raise HTTPException(404, "资料来源不存在")
        if payload.material_package_id is not None and not conn.execute(
            "SELECT 1 FROM material_packages WHERE id=? AND project_id=?", (payload.material_package_id, project_id)
        ).fetchone():
            raise HTTPException(422, "资料包不属于当前评审项目")
        role = payload.document_role or CATEGORY_DOCUMENT_ROLES.get(source["document_category"], "attachment")
        if role not in DOCUMENT_ROLES:
            raise HTTPException(422, f"不支持的资料角色: {role}")
        now = utc_now()
        conn.execute(
            """INSERT INTO project_documents(project_id,material_package_id,source_id,document_role,assignment_status,
               assignment_source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(project_id,source_id) DO UPDATE SET material_package_id=excluded.material_package_id,
               document_role=excluded.document_role,assignment_status=excluded.assignment_status,
               assignment_source='manual',updated_at=excluded.updated_at""",
            (project_id, payload.material_package_id, source_id, role,
             "manual_assigned" if payload.material_package_id is not None else "pending_assignment", "manual", now, now),
        )
        assignment = conn.execute(
            "SELECT * FROM project_documents WHERE project_id=? AND source_id=?", (project_id, source_id)
        ).fetchone()
    return dict(assignment)


@app.patch("/api/projects/{project_id}/sources/{source_id}/category")
def update_source_category(project_id: str, source_id: int, payload: DocumentCategoryUpdateRequest) -> dict[str, Any]:
    """Allow an expert to correct a low-confidence file classification."""
    category = payload.category.strip()
    if category not in DOCUMENT_CATEGORIES:
        raise HTTPException(422, f"不支持的资料分类: {category}")
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM document_sources WHERE id=? AND project_id=?",
            (source_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "资料来源不存在")
        conn.execute(
            """UPDATE document_sources
               SET document_category=?,category_confidence=1.0,
                   category_evidence=?,category_source='manual'
             WHERE id=? AND project_id=?""",
            (category, json.dumps(["manual_override"], ensure_ascii=False), source_id, project_id),
        )
        conn.execute(
            "UPDATE project_documents SET document_role=?,updated_at=? WHERE project_id=? AND source_id=?",
            (CATEGORY_DOCUMENT_ROLES.get(category, "attachment"), utc_now(), project_id, source_id),
        )
        # Keep specialty assessments consistent with the corrected source scope.
        if row["document_id"]:
            conn.execute(
                "DELETE FROM specialty_assessments WHERE project_id=? AND document_id=?",
                (project_id, row["document_id"]),
            )
            if category in SPECIALTY_ELIGIBLE_CATEGORIES:
                document = conn.execute("SELECT content FROM documents WHERE id=?", (row["document_id"],)).fetchone()
                if document and document["content"]:
                    _, _, assessment = infer_specialty(document["content"])
                    save_specialty_assessment(conn, project_id, row["document_id"], assessment)
        updated = conn.execute("SELECT * FROM document_sources WHERE id=?", (source_id,)).fetchone()
    return dict(updated)


@app.get("/api/projects/{project_id}/specialty")
def get_specialty_assessment(project_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM specialty_assessments WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()
    if not row:
        raise HTTPException(404, "项目尚未完成专业识别")
    return {
        "project_id": project_id,
        "primary": row["primary_specialty"],
        "related": json.loads(row["related_specialties_json"]),
        "confidence": row["confidence"],
        "status": row["status"],
        "scores": json.loads(row["scores_json"]),
        "details": json.loads(row["evidence_json"]),
        "method": row["method"],
        "created_at": row["created_at"],
    }


@app.post("/api/projects")
def create_project(name: str = Form(...), project_type: str = Form("技术改造")) -> dict[str, Any]:
    project_id = f"PRJ-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    with db() as conn:
        conn.execute("INSERT INTO projects(id,name,project_type,created_at) VALUES(?,?,?,?)", (project_id, name, project_type, utc_now()))
    return {"project_id": project_id, "name": name, "project_type": project_type, "status": "uploaded"}


@app.post("/api/projects/{project_id}/documents")
async def upload_document(project_id: str, file: UploadFile = File(...), document_type: str = Form("project_material"),
                          material_package_id: Annotated[int | None, Form()] = None) -> dict[str, Any]:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件不能超过50MB")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        if material_package_id is not None and not conn.execute(
            'SELECT 1 FROM material_packages WHERE id=? AND project_id=?', (material_package_id, project_id)
        ).fetchone():
            raise HTTPException(422, '所属报告不属于当前项目。')
    try:
        sources = expand_upload(file.filename or "unknown", data)
    except ValueError as exc:
        raise HTTPException(415, str(exc)) from exc
    digest = hashlib.sha256(data).hexdigest()
    project_upload_dir = UPLOAD_DIR / project_id
    project_upload_dir.mkdir(parents=True, exist_ok=True)
    stored_file = project_upload_dir / f"{digest[:16]}_{Path(file.filename or 'upload.bin').name}"
    stored_file.write_bytes(data)
    created_documents = 0
    reused_documents = 0
    total_chunks = 0
    assessments: list[dict[str, Any]] = []
    seen_sources: dict[str, dict[str, Any]] = {}
    with db() as conn:
        for source in sources:
            source_hash = hashlib.sha256(source["data"]).hexdigest()
            source_category = "other"
            source_category_confidence = 0.0
            source_category_evidence: list[str] = []
            if source["kind"] == "zip":
                result: dict[str, Any] = {
                    "document_id": None, "text": "", "kind": "zip", "status": "container",
                    "error": None, "method": "none", "converted_path": None, "page_count": None,
                }
            elif source_hash in seen_sources:
                result = seen_sources[source_hash].copy()
                if result["document_id"]:
                    reused_documents += 1
            else:
                existing = conn.execute(
                    "SELECT id,content FROM documents WHERE project_id=? AND file_hash=? ORDER BY id LIMIT 1",
                    (project_id, source_hash),
                ).fetchone()
                if existing:
                    metadata = conn.execute(
                        """SELECT file_kind,parse_method,converted_path,page_count FROM document_sources
                           WHERE project_id=? AND file_hash=? AND document_id=? ORDER BY id LIMIT 1""",
                        (project_id, source_hash, existing["id"]),
                    ).fetchone()
                    result = {
                        "document_id": existing["id"], "text": existing["content"],
                        "kind": metadata["file_kind"] if metadata else file_kind(source["filename"]),
                        "status": "parsed", "error": None,
                        "method": metadata["parse_method"] if metadata else "direct",
                        "converted_path": metadata["converted_path"] if metadata else None,
                        "page_count": metadata["page_count"] if metadata else None,
                    }
                    reused_documents += 1
                else:
                    prior_source = conn.execute(
                        """SELECT file_kind,parse_status,parse_error,parse_method,converted_path,page_count
                             FROM document_sources
                           WHERE project_id=? AND file_hash=? ORDER BY id LIMIT 1""",
                        (project_id, source_hash),
                    ).fetchone()
                    if prior_source and prior_source["parse_status"] == "parsed":
                        result = {
                            "document_id": None, "text": "", "kind": prior_source["file_kind"],
                            "status": prior_source["parse_status"], "error": prior_source["parse_error"],
                            "method": prior_source["parse_method"], "converted_path": prior_source["converted_path"],
                            "page_count": prior_source["page_count"],
                        }
                    else:
                        parsed = parse_source_file(source["filename"], source["data"])
                        result = {
                            "document_id": None, "text": parsed.text, "kind": parsed.kind,
                            "status": parsed.status, "error": parsed.error, "method": parsed.method,
                            "converted_path": persist_conversion(project_id, source_hash, parsed),
                            "page_count": parsed.page_count,
                        }
            source_category, source_category_confidence, source_category_evidence = classify_document(
                source["source_path"], source["filename"], result["text"],
            )
            if document_type == 'project_report' and source['archive_depth'] == 0 and source['kind'] != 'zip':
                source_category, source_category_confidence, source_category_evidence = 'project_report', 1.0, ['uploaded_as_report']
            if result["status"] == "parsed" and not result["document_id"]:
                cur = conn.execute(
                    """INSERT INTO documents(project_id,filename,document_type,file_hash,content,parse_status,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (project_id, source["filename"], document_type, source_hash, result["text"], "parsed", utc_now()),
                )
                result["document_id"] = cur.lastrowid
                chunks = split_chunks(result["text"])
                for section, chunk, page in chunks:
                    conn.execute(
                        "INSERT INTO chunks(project_id,document_id,chunk_type,section_path,text,page,created_at) VALUES(?,?,?,?,?,?,?)",
                        (project_id, result["document_id"], "project_fact", section, chunk, page, utc_now()),
                    )
                if source_category in SPECIALTY_ELIGIBLE_CATEGORIES:
                    specialty, _, specialty_result = infer_specialty(result["text"])
                    save_specialty_assessment(conn, project_id, result["document_id"], specialty_result)
                    assessments.append({"document_id": result["document_id"], "filename": source["filename"], "specialty": specialty, "result": specialty_result})
                created_documents += 1
                total_chunks += len(chunks)
            seen_sources[source_hash] = result.copy()
            source.update({
                "document_id": result["document_id"], "kind": result["kind"],
                "parse_status": result["status"], "parse_error": result["error"],
                "parse_method": result["method"], "document_category": source_category,
            })
            source_cursor = conn.execute(
                """INSERT INTO document_sources(
                     project_id,document_id,source_path,source_archive,archive_depth,filename,
                     file_hash,file_size,file_kind,document_category,category_confidence,category_evidence,
                     parse_status,parse_error,parse_method,converted_path,page_count,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    project_id, result["document_id"], source["source_path"], source["source_archive"],
                    source["archive_depth"], source["filename"], source_hash, len(source["data"]),
                    result["kind"], source_category, source_category_confidence, json.dumps(source_category_evidence, ensure_ascii=False),
                    result["status"], result["error"], result["method"], result["converted_path"],
                    result["page_count"], utc_now(),
                ),
            )
            auto_assign_source_to_package(
                conn, project_id, source_cursor.lastrowid, source_category, source["filename"], source["source_path"], result["text"],
                package_id=material_package_id,
            )
            source['visual_assets'] = collect_assets(conn, DATA_DIR, source_cursor.lastrowid,
                source['filename'], source['data'], result['converted_path'])
        parsed_count = sum(source["parse_status"] == "parsed" for source in sources)
        project_specialty = None
        if len(sources) == 1 and sources[0]["parse_status"] == "parsed":
            row = conn.execute(
                "SELECT primary_specialty FROM specialty_assessments WHERE document_id=? ORDER BY id DESC LIMIT 1",
                (sources[0]["document_id"],),
            ).fetchone()
            project_specialty = row["primary_specialty"] if row else None
        conn.execute(
            "UPDATE projects SET specialty=?, status=? WHERE id=?",
            (project_specialty, "ready" if parsed_count else "needs_material", project_id),
        )
    status_counts = {status: sum(source["parse_status"] == status for source in sources) for status in {source["parse_status"] for source in sources}}
    category_counts = {category: sum(source["document_category"] == category for source in sources) for category in {source["document_category"] for source in sources}}
    parse_method_counts = {method: sum(source["parse_method"] == method for source in sources) for method in {source["parse_method"] for source in sources}}
    logger.info("sources ingested project_id=%s upload=%s sources=%s parsed=%s", project_id, file.filename, len(sources), parsed_count)
    return {
        "filename": file.filename, "stored_path": str(stored_file.relative_to(DATA_DIR)),
        "source_files": len(sources), "parsed_files": parsed_count,
        "created_documents": created_documents, "reused_documents": reused_documents,
        "chunks": total_chunks, "status_counts": status_counts,
        "category_counts": category_counts, "parse_method_counts": parse_method_counts,
        "visual_assets": {"count": sum(s['visual_assets']['count'] for s in sources),
                          "needs_recognition": sum(s['visual_assets'].get('needs_recognition',0) for s in sources),
                          "incomplete_sources": sum(s['visual_assets']['status'] in ('partial','failed') for s in sources)},
        "assessments": assessments,
    }


@app.post("/api/projects/{project_id}/review")
def review_project(project_id: str, material_package_id: int | None = None) -> dict[str, Any]:
    with db() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            raise HTTPException(404, "项目不存在")
        if material_package_id is not None and not conn.execute("SELECT 1 FROM material_packages WHERE id=? AND project_id=?", (material_package_id, project_id)).fetchone():
            raise HTTPException(404, "资料包不存在")
        if not conn.execute("SELECT 1 FROM chunks WHERE project_id=?", (project_id,)).fetchone():
            raise HTTPException(409, "请先上传并解析项目材料")
        if material_package_id is None:
            conn.execute("DELETE FROM findings WHERE project_id=? AND material_package_id IS NULL", (project_id,))
        else:
            conn.execute("DELETE FROM findings WHERE project_id=? AND material_package_id=?", (project_id, material_package_id))
    findings, conclusion = build_findings(project_id, material_package_id)
    with db() as conn:
        for item in findings:
            conn.execute("INSERT INTO findings(project_id,dimension,severity,title,judgement,suggestion,evidence_json,conclusion,material_package_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (project_id, item["dimension"], item["severity"], item["title"], item["judgement"], item["suggestion"], json.dumps(item["evidence"], ensure_ascii=False), conclusion, material_package_id, utc_now()))
        conn.execute("UPDATE projects SET status='pending_expert' WHERE id=?", (project_id,))
    return {"project_id": project_id, "material_package_id": material_package_id, "specialty": project["specialty"], "conclusion_suggestion": conclusion, "findings": findings, "requires_expert_confirmation": True}


ALLOWED_CONCLUSIONS = {"一致通过", "少量修改后通过", "大量修改后通过", "不通过"}
ALLOWED_ACTIONS = {"pending", "accepted", "modified", "rejected"}


def review_issue_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["evidence"] = json.loads(item.pop("evidence_json", "[]") or "[]")
    return item


def session_payload(conn: sqlite3.Connection, session_id: int) -> dict[str, Any]:
    session = conn.execute("SELECT * FROM review_sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        raise HTTPException(404, "评审会话不存在")
    issues = [review_issue_dict(row) for row in conn.execute("SELECT * FROM review_issues WHERE session_id=? ORDER BY id", (session_id,)).fetchall()]
    stats = {
        "total": len(issues),
        "reviewed": sum(issue["action"] != "pending" for issue in issues),
        "accepted": sum(issue["action"] == "accepted" for issue in issues),
        "modified": sum(issue["action"] == "modified" for issue in issues),
        "rejected": sum(issue["action"] == "rejected" for issue in issues),
        "pending": sum(issue["action"] == "pending" for issue in issues),
    }
    result = dict(session)
    result["issues"] = issues
    result["stats"] = stats
    result["report"] = None
    if session["report_id"]:
        report = conn.execute("SELECT id,filename,created_at FROM reports WHERE id=?", (session["report_id"],)).fetchone()
        if report:
            result["report"] = {**dict(report), "download_url": f"/api/review-sessions/{session_id}/report"}
    return result


def create_review_session_record(project_id: str, material_package_id: int | None = None) -> dict[str, Any]:
    with db() as conn:
        project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            raise HTTPException(404, "项目不存在")
        existing = conn.execute(
            "SELECT id FROM review_sessions WHERE project_id=? AND material_package_id IS ? AND status != 'archived' ORDER BY id DESC LIMIT 1",
            (project_id, material_package_id),
        ).fetchone()
        if existing:
            return session_payload(conn, existing["id"])
        finding_params: tuple[Any, ...]
        if material_package_id is None:
            rows = conn.execute("SELECT * FROM findings WHERE project_id=? AND material_package_id IS NULL ORDER BY id", (project_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM findings WHERE project_id=? AND material_package_id=? ORDER BY id", (project_id, material_package_id)).fetchall()
        if not rows:
            raise HTTPException(409, "请先生成 AI 预评审结果")
        conclusion = rows[0]["conclusion"]
        cur = conn.execute(
            "INSERT INTO review_sessions(project_id,material_package_id,ai_conclusion,status,created_at) VALUES(?,?,?,?,?)",
            (project_id, material_package_id, conclusion, "expert_review", utc_now()),
        )
        session_id = cur.lastrowid
        for row in rows:
            conn.execute(
                """INSERT INTO review_issues(session_id,source_finding_id,dimension,title,ai_level,expert_level,
                   ai_opinion,expert_opinion,suggestion,evidence_json,action,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id, row["id"], row["dimension"], row["title"], row["severity"], row["severity"],
                    row["judgement"], row["judgement"], row["suggestion"], row["evidence_json"], "pending", utc_now(),
                ),
            )
        return session_payload(conn, session_id)


def generate_review_report(session_id: int) -> dict[str, Any]:
    with db() as conn:
        session = conn.execute("SELECT * FROM review_sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            raise HTTPException(404, "评审会话不存在")
        project = conn.execute("SELECT * FROM projects WHERE id=?", (session["project_id"],)).fetchone()
        issues = [review_issue_dict(row) for row in conn.execute("SELECT * FROM review_issues WHERE session_id=? ORDER BY id", (session_id,)).fetchall()]
        sources = [dict(row) for row in conn.execute(
            """SELECT filename,source_path,file_kind,document_category,parse_status
               FROM document_sources WHERE project_id=? ORDER BY id""",
            (session["project_id"],),
        ).fetchall()]
        stats = {
            "total": len(issues),
            "accepted": sum(item["action"] == "accepted" for item in issues),
            "modified": sum(item["action"] == "modified" for item in issues),
            "rejected": sum(item["action"] == "rejected" for item in issues),
        }
        document = Document()
        document.add_heading(f"{project['name']}项目智能预评审意见", level=0)
        document.add_paragraph(f"项目编号：{project['id']}")
        document.add_paragraph(f"专业：{project['specialty'] or '待确认'}    项目类型：{project['project_type'] or '技术改造'}")
        document.add_heading("一、项目基本情况", level=1)
        document.add_paragraph("本报告由智能预评审结果和专家复核意见自动生成，最终意见以专家确认结果为准。")
        document.add_heading("二、项目材料与依据", level=1)
        document.add_paragraph(f"本项目共纳入{len(sources)}份来源材料，材料解析、分类和项目资料包归属以系统记录为准。")
        if sources:
            for index, source in enumerate(sources, start=1):
                document.add_paragraph(
                    f"{index}. {source['filename'] or source['source_path'] or '未命名文件'}"
                    f"（{source['document_category'] or '其他附件'}，{source['parse_status'] or '未知'}）"
                )
        document.add_heading("三、智能预评审结果", level=1)
        document.add_paragraph(f"AI初步建议：{session['ai_conclusion']}。共识别评审事项{stats['total']}项。")
        document.add_heading("四、专家复核意见", level=1)
        document.add_paragraph(f"专家复核：直接采纳{stats['accepted']}项，修改后采纳{stats['modified']}项，驳回{stats['rejected']}项。")
        if session["expert_note"]:
            document.add_paragraph(f"专家说明：{session['expert_note']}")
        document.add_heading("五、主要评审问题", level=1)
        included = [item for item in issues if item["action"] != "rejected"]
        if not included:
            document.add_paragraph("本次复核未保留需要纳入报告的评审问题。")
        for index, item in enumerate(included, start=1):
            document.add_heading(f"{index}. {item['title']}", level=2)
            document.add_paragraph(f"专业维度：{item['dimension']}；问题等级：{item['expert_level']}")
            document.add_paragraph(f"专家意见：{item['expert_opinion'] or item['ai_opinion']}")
            if item["suggestion"]:
                document.add_paragraph(f"处理建议：{item['suggestion']}")
            evidence = item.get("evidence") or []
            if evidence:
                document.add_paragraph(f"原文证据：{evidence[0].get('text', '')[:500] if isinstance(evidence[0], dict) else str(evidence[0])}")
        document.add_heading("六、最终评审结论", level=1)
        document.add_paragraph(f"{session['expert_conclusion'] or session['ai_conclusion']}。")
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"评审报告-{session['project_id']}-{timestamp}.docx"
        path = REPORT_DIR / filename
        document.save(path)
        conn.execute("DELETE FROM reports WHERE session_id=?", (session_id,))
        report_id = conn.execute(
            "INSERT INTO reports(session_id,project_id,filename,path,created_at) VALUES(?,?,?,?,?)",
            (session_id, session["project_id"], filename, str(path), utc_now()),
        ).lastrowid
        conn.execute("UPDATE review_sessions SET report_id=? WHERE id=?", (report_id, session_id))
    return {"id": report_id, "filename": filename, "download_url": f"/api/review-sessions/{session_id}/report"}


@app.post("/api/projects/{project_id}/review-session")
def create_review_session(project_id: str, material_package_id: int | None = None) -> dict[str, Any]:
    return create_review_session_record(project_id, material_package_id)


@app.get("/api/projects/{project_id}/review-session/latest")
def get_latest_review_session(project_id: str, material_package_id: int | None = None) -> dict[str, Any]:
    with db() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        if material_package_id is None:
            row = conn.execute(
                "SELECT id FROM review_sessions WHERE project_id=? ORDER BY id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM review_sessions WHERE project_id=? AND material_package_id=? ORDER BY id DESC LIMIT 1",
                (project_id, material_package_id),
            ).fetchone()
        if not row:
            raise HTTPException(404, "当前项目尚无评审会话")
        return session_payload(conn, row["id"])


@app.get("/api/review-sessions/{session_id}")
def get_review_session(session_id: int) -> dict[str, Any]:
    with db() as conn:
        return session_payload(conn, session_id)


@app.patch("/api/review-sessions/{session_id}/issues/{issue_id}")
def update_review_issue(session_id: int, issue_id: int, payload: ReviewIssueUpdateRequest) -> dict[str, Any]:
    if payload.action not in ALLOWED_ACTIONS:
        raise HTTPException(400, "无效的专家处理动作")
    with db() as conn:
        row = conn.execute("SELECT * FROM review_issues WHERE id=? AND session_id=?", (issue_id, session_id)).fetchone()
        if not row:
            raise HTTPException(404, "评审问题不存在")
        before = dict(row)
        values = {
            "action": payload.action,
            "expert_level": payload.expert_level or row["expert_level"],
            "expert_opinion": payload.expert_opinion if payload.expert_opinion is not None else row["expert_opinion"],
            "expert_note": payload.expert_note if payload.expert_note is not None else row["expert_note"],
            "title": payload.title or row["title"],
            "suggestion": payload.suggestion if payload.suggestion is not None else row["suggestion"],
            "updated_at": utc_now(),
        }
        conn.execute(
            """UPDATE review_issues SET action=?,expert_level=?,expert_opinion=?,expert_note=?,title=?,suggestion=?,updated_at=?
               WHERE id=? AND session_id=?""",
            (values["action"], values["expert_level"], values["expert_opinion"], values["expert_note"], values["title"], values["suggestion"], values["updated_at"], issue_id, session_id),
        )
        after = dict(conn.execute("SELECT * FROM review_issues WHERE id=?", (issue_id,)).fetchone())
        conn.execute("INSERT INTO review_action_log(session_id,issue_id,action,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)", (session_id, issue_id, payload.action, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), utc_now()))
        return {"issue": review_issue_dict(conn.execute("SELECT * FROM review_issues WHERE id=?", (issue_id,)).fetchone()), "session": session_payload(conn, session_id)}


@app.post("/api/review-sessions/{session_id}/confirm")
def confirm_review_session(session_id: int, payload: ReviewConfirmRequest) -> dict[str, Any]:
    if payload.expert_conclusion not in ALLOWED_CONCLUSIONS:
        raise HTTPException(400, "无效的评审结论")
    with db() as conn:
        session = conn.execute("SELECT * FROM review_sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            raise HTTPException(404, "评审会话不存在")
        conn.execute("UPDATE review_sessions SET expert_conclusion=?,status='expert_confirmed',expert_note=?,confirmed_at=? WHERE id=?", (payload.expert_conclusion, payload.expert_note, utc_now(), session_id))
        conn.execute("UPDATE projects SET status='expert_confirmed' WHERE id=?", (session["project_id"],))
    report = generate_review_report(session_id)
    with db() as conn:
        conn.execute("UPDATE review_sessions SET report_id=? WHERE id=?", (report["id"], session_id))
        result = session_payload(conn, session_id)
    result["report"] = report
    return result


@app.get("/api/review-sessions/{session_id}/report")
def download_review_report(session_id: int) -> FileResponse:
    with db() as conn:
        session = conn.execute("SELECT report_id FROM review_sessions WHERE id=?", (session_id,)).fetchone()
        if not session or not session["report_id"]:
            raise HTTPException(404, "报告尚未生成")
        report = conn.execute("SELECT * FROM reports WHERE id=?", (session["report_id"],)).fetchone()
    if not report or not Path(report["path"]).exists():
        raise HTTPException(404, "报告文件不存在")
    return FileResponse(report["path"], filename=report["filename"], media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.post("/api/review-sessions/{session_id}/archive")
def archive_review_session(session_id: int) -> dict[str, Any]:
    with db() as conn:
        session = conn.execute("SELECT * FROM review_sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            raise HTTPException(404, "评审会话不存在")
        if session["status"] != "expert_confirmed":
            raise HTTPException(409, "请先确认专家结论")
        conn.execute("UPDATE review_sessions SET status='archived',archived_at=? WHERE id=?", (utc_now(), session_id))
        conn.execute("UPDATE projects SET status='archived' WHERE id=?", (session["project_id"],))
        return session_payload(conn, session_id)


@app.get("/api/analytics/overview")
def analytics_overview() -> dict[str, Any]:
    with db() as conn:
        projects = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        latest_specialty = {row["project_id"]: row["primary_specialty"] for row in conn.execute("SELECT project_id,primary_specialty FROM specialty_assessments WHERE id IN (SELECT MAX(id) FROM specialty_assessments GROUP BY project_id)").fetchall()}
        session_rows = conn.execute("SELECT project_id,expert_conclusion,status FROM review_sessions ORDER BY id").fetchall()
        latest_sessions: dict[str, sqlite3.Row] = {}
        for row in session_rows:
            latest_sessions[row["project_id"]] = row
        counts: dict[str, dict[str, Any]] = {code: {"code": code, "name": profile["name"], "project_count": 0, "report_count": 0, "expert_opinion_count": 0, "conclusions": {key: 0 for key in ["一致通过", "少量修改后通过", "大量修改后通过", "不通过", "待标注"]}} for code, profile in SPECIALTY_PROFILES.items()}
        for project in projects:
            code = project["specialty"] or latest_specialty.get(project["id"])
            if code not in counts:
                continue
            item = counts[code]
            item["project_count"] += 1
            session = latest_sessions.get(project["id"])
            if session and session["expert_conclusion"]:
                item["expert_opinion_count"] += 1
                item["conclusions"][session["expert_conclusion"]] = item["conclusions"].get(session["expert_conclusion"], 0) + 1
            else:
                item["conclusions"]["待标注"] += 1
            if session and session["status"] in {"expert_confirmed", "archived"}:
                item["report_count"] += 1
        type_counts: dict[str, int] = {}
        for project in projects:
            project_type = project["project_type"] or "待标注"
            type_counts[project_type] = type_counts.get(project_type, 0) + 1
    return {"specialties": list(counts.values()), "project_type_counts": type_counts, "total_projects": len(projects), "generated_at": utc_now()}


@app.get("/api/projects/{project_id}/findings")
def list_findings(project_id: str, material_package_id: int | None = None) -> dict[str, Any]:
    with db() as conn:
        if material_package_id is None:
            rows = conn.execute("SELECT * FROM findings WHERE project_id=? AND material_package_id IS NULL ORDER BY id", (project_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM findings WHERE project_id=? AND material_package_id=? ORDER BY id", (project_id, material_package_id)).fetchall()
    return {"project_id": project_id, "findings": [{**dict(row), "evidence": json.loads(row["evidence_json"])} for row in rows]}


from workbench import Workbench

workbench = Workbench(db, KNOWLEDGE_RULES)
app.include_router(workbench.router)
app.include_router(assets_router(db, lambda: DATA_DIR))
from review_agent import ReviewAgent

def default_model_config():
    return {
    'enabled': LLM_ENABLED, 'key': LLM_API_KEY, 'model': LLM_MODEL,
    'url': LLM_BASE_URL if LLM_BASE_URL.endswith('/chat/completions') else f"{LLM_BASE_URL}/chat/completions",
    }
from model_gateway import ModelGateway
from hybrid_retrieval import HybridRetriever
from model_settings import ModelSettings, ModelActivityMiddleware

model_settings = ModelSettings(default_model_config, DATA_DIR / 'model-settings.json')
model_config = model_settings.config
app.include_router(model_settings.router)
app.add_middleware(ModelActivityMiddleware, settings=model_settings)

model_gateway = ModelGateway(model_config)
workbench.retriever = HybridRetriever(model_gateway, lambda: os.getenv('LLM_RETRIEVAL_ENABLED','true').lower() not in ('0','false','no'))
review_agent = ReviewAgent(workbench, model_config, model_gateway)
app.include_router(review_agent.router)
from batch_review import BatchReviews

batch_reviews = BatchReviews(workbench, lambda project_id, file: upload_document(project_id, file, document_type='project_material'),
    lambda project_id, source_id, category: update_source_category(project_id, source_id, DocumentCategoryUpdateRequest(category=category)))
app.include_router(batch_reviews.router)

from accounts import Accounts, AccountMiddleware
accounts = Accounts(db, lambda: DATA_DIR)
accounts.model_status = model_settings.status
workbench.access = accounts
app.include_router(accounts.router)
app.add_middleware(AccountMiddleware, accounts=accounts, routes=lambda: app.routes)
app.add_middleware(CORSMiddleware, allow_origin_regex=r'https?://(127\.0\.0\.1|localhost)(:\d+)?',
                   allow_credentials=True, allow_methods=['*'], allow_headers=['*'])


@app.post('/api/workbench/brief/import')
def import_brief_file(file: UploadFile = File(...)):
    """Parse a task document into editable requirements, without ingesting report evidence."""
    filename=Path((file.filename or '').replace('\\','/')).name
    ext=Path(filename).suffix.lower()
    if ext not in {'.docx','.doc','.pdf','.txt','.md'}:
        raise HTTPException(422,'课题要求支持 Word、PDF、TXT 和 Markdown 文件。')
    data=file.file.read(MAX_UPLOAD_BYTES+1)
    if not data:raise HTTPException(422,'所选文件为空，请重新选择。')
    if len(data)>MAX_UPLOAD_BYTES:raise HTTPException(413,'课题文件不能超过 50 MB。')
    try:
        if ext in {'.txt','.md'}:
            try:text=data.decode('utf-8-sig')
            except UnicodeDecodeError:text=data.decode('gb18030')
            method='text'
        else:
            # Short task PDFs may contain fewer than the report OCR threshold's 100 characters.
            text=extract_pdf(data)[0] if ext=='.pdf' else ''
            method='direct'
            if not re.sub(r'\[第\d+页\]','',text).strip():
                parsed=parse_source_file(filename,data)
                if parsed.status!='parsed':
                    message={'.doc':'旧版 Word 转换失败，请另存为 .docx 后上传。',
                             '.pdf':'文件未能解析出文字；扫描版 PDF 请先转为可复制文字的 PDF 或 Word。',
                             '.docx':'Word 文件解析失败或没有正文，请检查文件内容后重新上传。'}[ext]
                    raise HTTPException(422,message)
                text,method=parsed.text,parsed.method
        if not text.strip():raise HTTPException(422,'文件中没有可读取的课题要求文字。')
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422,'文件解析失败，请检查文件是否损坏，或转换为 DOCX、可复制文字的 PDF、TXT 后上传。') from exc
    return {'filename':filename,'text':text.strip(),'characters':len(text.strip()),'method':method}
