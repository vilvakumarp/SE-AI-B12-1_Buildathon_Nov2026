"""Document ingestion and lightweight BM25 retrieval for the support desk."""
import json
import re
import time
import hashlib
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from docx import Document
from pypdf import PdfReader
from openpyxl import load_workbook
from dotenv import load_dotenv

load_dotenv()

KB_DIR = Path("knowledge_base")
INDEX_PATH = KB_DIR / "bm25_index.json"
CHROMA_DIR = KB_DIR / "chroma_db"
COLLECTION_NAME = "support_sop"
EMBEDDING_MODEL = __import__("os").getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
STOPWORDS = {"a", "an", "and", "are", "can", "do", "for", "from", "get", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or", "the", "to", "what", "with", "you"}


def _tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2 and t not in STOPWORDS]


def _paragraph_chunks(title, paragraphs, source):
    chunks, current = [], []
    for paragraph in paragraphs:
        value = re.sub(r"\s+", " ", paragraph).strip()
        if not value:
            continue
        current.append(value)
        if len(" ".join(current)) >= 700:
            chunks.append({"text": f"{title}\n" + " ".join(current), "source": source, "title": title})
            current = []
    if current:
        chunks.append({"text": f"{title}\n" + " ".join(current), "source": source, "title": title})
    return chunks


def extract_docx(path):
    doc = Document(path)
    paragraphs = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        paragraphs.append("\n".join(" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows))
    links = [rel.target_ref for rel in doc.part.rels.values() if rel.reltype.endswith("/hyperlink")]
    return _paragraph_chunks(Path(path).stem, paragraphs, str(path)), sorted(set(links))


def extract_pdf(path):
    reader = PdfReader(path)
    paragraphs = [(page.extract_text() or "") for page in reader.pages]
    return _paragraph_chunks(Path(path).stem, paragraphs, str(path)), []


def extract_xlsx(path):
    workbook = load_workbook(path, read_only=True, data_only=True)
    paragraphs = []
    for sheet in workbook.worksheets:
        rows = []
        for row in sheet.iter_rows(values_only=True):
            values = [str(value).strip() for value in row if value is not None and str(value).strip()]
            if values:
                rows.append(" | ".join(values))
        paragraphs.append(f"Sheet: {sheet.title}\n" + "\n".join(rows))
    return _paragraph_chunks(Path(path).stem, paragraphs, str(path)), []


def extract_text(path):
    return _paragraph_chunks(Path(path).stem, [Path(path).read_text(encoding="utf-8")], str(path)), []


def extract_file(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".docx": return extract_docx(path)
    if suffix == ".pdf": return extract_pdf(path)
    if suffix in {".xlsx", ".xlsm"}: return extract_xlsx(path)
    return extract_text(path)


def scrape_links(urls, timeout=15):
    chunks, report = [], []
    headers = {"User-Agent": "SupportDeskAssignment/1.0 (educational RAG demo)"}
    for url in sorted(set(urls)):
        item = {"url": url, "status": "failed", "error": "", "title": ""}
        try:
            response = None
            last_error = None
            for attempt in range(3):
                try:
                    response = requests.get(url, headers=headers, timeout=timeout)
                    response.raise_for_status()
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < 2: time.sleep(1 + attempt)
            if response is None: raise last_error
            soup = BeautifulSoup(response.text, "html.parser")
            for node in soup(["script", "style", "noscript"]): node.decompose()
            title = soup.title.get_text(" ", strip=True) if soup.title else url
            body = soup.get_text(" ", strip=True)
            if len(body) < 100: raise ValueError("Page returned too little readable text")
            chunks.extend(_paragraph_chunks(title, [body], url))
            item.update(status="success", title=title)
        except Exception as exc:
            item["error"] = str(exc)
        report.append(item)
    return chunks, report


def build_index(files=None, scrape=False):
    KB_DIR.mkdir(exist_ok=True)
    all_chunks, links, source_manifest = [], [], []
    # ChromaDB is the knowledge-base source of truth. Only the supplied input
    # corpus and files uploaded through the admin page are ingested by default.
    default_files = sorted((KB_DIR / "uploads").rglob("*")) if (KB_DIR / "uploads").exists() else []
    input_dirs = [Path("input"), Path("Input")]
    for input_dir in input_dirs:
        if input_dir.exists(): default_files.extend(sorted(input_dir.glob("*")))
    seen_paths = set()
    seen_digests = set()
    for path in files or default_files:
        path = Path(path)
        path_key = str(path.resolve()).casefold()
        if path_key in seen_paths: continue
        seen_paths.add(path_key)
        if Path(path).name in {INDEX_PATH.name, "link_report.json", "chroma_error.txt"} or not Path(path).is_file(): continue
        try:
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if digest in seen_digests:
                continue
            seen_digests.add(digest)
            chunks, found_links = extract_file(path)
            all_chunks.extend(chunks); links.extend(found_links)
            source_manifest.append({"source": str(path), "sha256": digest, "chunks": len(chunks)})
        except Exception:
            continue
    report = []
    if scrape and links:
        web_chunks, report = scrape_links(links)
        all_chunks.extend(web_chunks)
    payload = {"created_at": datetime.now(timezone.utc).isoformat(), "chunks": all_chunks, "links": sorted(set(links)), "link_report": report, "sources": source_manifest}
    INDEX_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (KB_DIR / "link_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _chroma_collection.cache_clear()
    _build_chroma(all_chunks)
    _chroma_collection.cache_clear()
    return payload


def _build_chroma(chunks):
    """Persist chunks in ChromaDB; BM25 JSON remains available as a fallback."""
    try:
        import chromadb
        from chromadb.utils import embedding_functions
        embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        collection = client.get_or_create_collection(COLLECTION_NAME, embedding_function=embedding_function)
        if collection.count():
            client.delete_collection(COLLECTION_NAME)
            collection = client.get_or_create_collection(COLLECTION_NAME, embedding_function=embedding_function)
        if chunks:
            collection.add(
                ids=[f"chunk-{i}" for i in range(len(chunks))],
                documents=[chunk["text"] for chunk in chunks],
                metadatas=[{"source": chunk["source"], "title": chunk["title"]} for chunk in chunks],
            )
    except Exception as exc:
        # Keep ingestion usable when the optional vector-store dependency or
        # its local embedding model is unavailable.
        (KB_DIR / "chroma_error.txt").write_text(str(exc), encoding="utf-8")


def _load_index():
    if not INDEX_PATH.exists(): build_index()
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))

@lru_cache(maxsize=1)
def _chroma_collection():
    """Load the embedding model, Chroma client, and collection once per process."""
    import chromadb
    from chromadb.utils import embedding_functions
    embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_collection(COLLECTION_NAME, embedding_function=embedding_function)


def retrieve(query, category, limit=3):
    index = _load_index(); chunks = index.get("chunks", [])
    try:
        collection = _chroma_collection()
        result = collection.query(query_texts=[query + " " + category], n_results=limit)
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        matches = []
        for document, metadata, distance in zip(documents, metadatas, distances):
            matches.append((round(1.0 / (1.0 + float(distance)), 3), {"text": document, **metadata}))
        if matches and any(set(_tokens(query)) & set(_tokens(item[1].get("text", ""))) for item in matches):
            return matches, index
    except Exception:
        pass
    query_terms = set(_tokens(query + " " + category))
    scored = []
    doc_count = len(chunks) or 1
    for chunk in chunks:
        terms = _tokens(chunk.get("text", "")); term_set = set(terms)
        if not term_set: continue
        overlap = len(query_terms & term_set)
        if not overlap: continue
        # BM25-style score with document frequency and length normalization.
        score = 0.0
        avg_len = sum(len(_tokens(c.get("text", ""))) for c in chunks) / doc_count
        for term in query_terms:
            tf = terms.count(term)
            df = sum(term in set(_tokens(c.get("text", ""))) for c in chunks)
            idf = max(0.0, __import__("math").log((doc_count - df + 0.5) / (df + 0.5) + 1))
            score += idf * ((tf * 2.0) / (tf + 1.5 * (0.25 + 0.75 * len(terms) / max(avg_len, 1))))
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [(round(score, 3), chunk) for score, chunk in scored[:limit]], index
