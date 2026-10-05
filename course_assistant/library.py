"""The course library: stored documents, their chunks, and the three search indexes.

- Keyword index (BM25, via bm25s) over text chunks
- Text embedding index (chromadb collection "text_chunks")
- Visual embedding index (chromadb collection "page_images"), kept separate

Everything lives under the data folder, so the library survives restarts.
Removing a document deletes its files, chunks and index entries.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import bm25s
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .ingest import IngestError, Page, file_hash, read_document
from .services import ImageEmbedder, ServiceError, TextEmbedder

CHUNK_SIZE = 700
CHUNK_OVERLAP = 100


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_name: str
    page: int
    label: str
    title: str
    text: str
    image_path: str = ""

    @property
    def location(self) -> str:
        return f"{self.label} {self.page}"

    def search_text(self) -> str:
        """Text used for keyword and embedding search, with its source for context."""
        return f"{self.doc_name}, {self.location}: {self.title}\n{self.text}"


@dataclass
class Document:
    doc_id: str
    name: str
    file_hash: str
    file_type: str
    added: float
    pages: list[Page] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def page_label(self) -> str:
        return self.pages[0].label if self.pages else "page"

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "name": self.name,
            "file_hash": self.file_hash,
            "file_type": self.file_type,
            "added": self.added,
            "pages": [p.to_dict() for p in self.pages],
            "chunks": [c.__dict__ for c in self.chunks],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        return cls(
            doc_id=data["doc_id"],
            name=data["name"],
            file_hash=data["file_hash"],
            file_type=data["file_type"],
            added=data["added"],
            pages=[Page(**p) for p in data["pages"]],
            chunks=[Chunk(**c) for c in data["chunks"]],
        )


@dataclass
class AddResult:
    document: Document
    added: bool  # False when the same file was already in the library
    message: str


def chunk_pages(doc_id: str, doc_name: str, pages: list[Page]) -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    chunks: list[Chunk] = []
    for page in pages:
        if not page.text.strip():
            continue  # image-only slides are still found through the visual index
        for index, piece in enumerate(splitter.split_text(page.text)):
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}:{page.number}:{index}",
                    doc_id=doc_id,
                    doc_name=doc_name,
                    page=page.number,
                    label=page.label,
                    title=page.title,
                    text=piece,
                    image_path=page.image_path,
                )
            )
    return chunks


class Library:
    def __init__(
        self,
        data_dir: str | Path,
        text_embedder: TextEmbedder,
        image_embedder: ImageEmbedder,
        soffice: str = "soffice",
        page_reader=None,
    ):
        self.data_dir = Path(data_dir)
        self.docs_dir = self.data_dir / "docs"
        self.manifest_path = self.data_dir / "library.json"
        self.text_embedder = text_embedder
        self.image_embedder = image_embedder
        self.soffice = soffice
        self.page_reader = page_reader  # optional document-parsing service
        self._lock = threading.RLock()
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.data_dir / "chroma"))
        self.documents: dict[str, Document] = {}
        self._embedders: dict[str, str] = {}
        self._load()
        self._open_collections()
        self._bm25: bm25s.BM25 | None = None
        self._bm25_chunks: list[Chunk] = []
        if self._embedders != self._embedder_names():
            self.reindex()
        self._rebuild_keyword_index()

    # -- persistence ---------------------------------------------------------

    def _embedder_names(self) -> dict[str, str]:
        return {"text": self.text_embedder.name, "image": self.image_embedder.name}

    def _load(self) -> None:
        if self.manifest_path.exists():
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            self.documents = {d["doc_id"]: Document.from_dict(d) for d in data.get("documents", [])}
            self._embedders = data.get("embedders", {})
        else:
            self._embedders = self._embedder_names()

    def _save(self) -> None:
        data = {
            "embedders": self._embedders,
            "documents": [d.to_dict() for d in self.documents.values()],
        }
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.manifest_path)

    def _open_collections(self) -> None:
        options = {"hnsw:space": "cosine"}
        self.text_index = self._client.get_or_create_collection("text_chunks", metadata=options, embedding_function=None)
        self.image_index = self._client.get_or_create_collection("page_images", metadata=options, embedding_function=None)

    # -- queries -------------------------------------------------------------

    def list_documents(self) -> list[Document]:
        return sorted(self.documents.values(), key=lambda d: d.name.lower())

    def all_chunks(self, doc_ids: list[str] | None = None) -> list[Chunk]:
        docs = [self.documents[i] for i in doc_ids if i in self.documents] if doc_ids else self.list_documents()
        return [c for d in docs for c in d.chunks]

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        doc = self.documents.get(chunk_id.split(":")[0])
        if doc:
            for chunk in doc.chunks:
                if chunk.chunk_id == chunk_id:
                    return chunk
        return None

    def get_page(self, doc_id: str, number: int) -> Page | None:
        doc = self.documents.get(doc_id)
        if doc:
            for page in doc.pages:
                if page.number == number:
                    return page
        return None

    # -- adding and removing -------------------------------------------------

    def add_file(self, path: str | Path, display_name: str | None = None) -> AddResult:
        path = Path(path)
        name = display_name or path.name
        digest = file_hash(path)
        with self._lock:
            for doc in self.documents.values():
                if doc.file_hash == digest:
                    return AddResult(doc, False, f"{name} is already in the library (as {doc.name}), so it wasn't added again.")
            doc_id = digest[:12]
            doc_dir = self.docs_dir / doc_id
            if doc_dir.exists():
                shutil.rmtree(doc_dir)
            doc_dir.mkdir(parents=True)
            stored = doc_dir / f"original{path.suffix.lower()}"
            shutil.copyfile(path, stored)
            try:
                pages = read_document(stored, doc_dir, soffice=self.soffice)
                notes = self._read_image_text(pages)
                doc = Document(
                    doc_id=doc_id,
                    name=name,
                    file_hash=digest,
                    file_type=path.suffix.lower().lstrip("."),
                    added=time.time(),
                    pages=pages,
                )
                doc.chunks = chunk_pages(doc_id, name, pages)
                self._index_document(doc)
            except (IngestError, ServiceError):
                shutil.rmtree(doc_dir, ignore_errors=True)
                self._delete_from_indexes(doc_id)
                raise
            self.documents[doc_id] = doc
            self._save()
            self._rebuild_keyword_index()
            count = len(pages)
            message = f"Added {name} ({count} {doc.page_label}{'s' if count != 1 else ''})."
            return AddResult(doc, True, " ".join([message, *notes]))

    def remove(self, doc_id: str) -> str:
        with self._lock:
            doc = self.documents.pop(doc_id, None)
            if doc is None:
                return "That document isn't in the library."
            self._delete_from_indexes(doc_id)
            shutil.rmtree(self.docs_dir / doc_id, ignore_errors=True)
            self._save()
            self._rebuild_keyword_index()
            return f"Removed {doc.name} and everything searchable from it."

    def _read_image_text(self, pages: list[Page]) -> list[str]:
        """Use the parsing service, if set, to read text from pages that are mostly pictures."""
        if self.page_reader is None:
            return []
        read = 0
        for page in pages:
            if page.image_path and len(page.text) < 40:
                try:
                    extra = self.page_reader.read(page.image_path)
                except ServiceError as exc:
                    return [f"Text inside images wasn't read ({exc})."]
                if extra:
                    page.text = (page.text + "\n[Text read from the image]\n" + extra).strip()
                    page.title = page.title or extra.splitlines()[0][:120]
                    read += 1
        return [f"Read text from {read} image-only {pages[0].label}s."] if read else []

    # -- indexing ------------------------------------------------------------

    def _index_document(self, doc: Document) -> None:
        if doc.chunks:
            vectors = self.text_embedder.embed_texts([c.search_text() for c in doc.chunks])
            self.text_index.add(
                ids=[c.chunk_id for c in doc.chunks],
                embeddings=vectors,
                metadatas=[{"doc_id": doc.doc_id, "page": c.page} for c in doc.chunks],
            )
        pages = [p for p in doc.pages if p.image_path]
        if pages:
            vectors = self.image_embedder.embed_images([p.image_path for p in pages], [p.text for p in pages])
            self.image_index.add(
                ids=[f"{doc.doc_id}:{p.number}" for p in pages],
                embeddings=vectors,
                metadatas=[{"doc_id": doc.doc_id, "page": p.number} for p in pages],
            )

    def _delete_from_indexes(self, doc_id: str) -> None:
        self.text_index.delete(where={"doc_id": doc_id})
        self.image_index.delete(where={"doc_id": doc_id})

    def reindex(self) -> None:
        """Rebuild both vector indexes, e.g. after switching embedding models."""
        with self._lock:
            for name in ("text_chunks", "page_images"):
                try:
                    self._client.delete_collection(name)
                except Exception:
                    pass
            self._open_collections()
            for doc in self.documents.values():
                self._index_document(doc)
            self._embedders = self._embedder_names()
            self._save()

    def _rebuild_keyword_index(self) -> None:
        chunks = self.all_chunks()
        self._bm25_chunks = chunks
        if not chunks:
            self._bm25 = None
            return
        retriever = bm25s.BM25()
        retriever.index(bm25s.tokenize([c.search_text() for c in chunks], stopwords="en", show_progress=False), show_progress=False)
        self._bm25 = retriever

    # -- search --------------------------------------------------------------

    def keyword_search(self, query: str, k: int, doc_ids: list[str] | None = None) -> list[tuple[Chunk, float]]:
        if self._bm25 is None:
            return []
        query_tokens = bm25s.tokenize([query], stopwords="en", show_progress=False)
        if not query_tokens.vocab:
            return []
        total = len(self._bm25_chunks)
        results, scores = self._bm25.retrieve(query_tokens, k=total, show_progress=False)
        hits = []
        for index, score in zip(results[0], scores[0]):
            chunk = self._bm25_chunks[int(index)]
            if score <= 0 or (doc_ids and chunk.doc_id not in doc_ids):
                continue
            hits.append((chunk, float(score)))
            if len(hits) >= k:
                break
        return hits

    def _where(self, doc_ids: list[str] | None) -> dict | None:
        if not doc_ids:
            return None
        return {"doc_id": {"$in": list(doc_ids)}}

    def text_vector_search(self, query: str, k: int, doc_ids: list[str] | None = None) -> list[tuple[Chunk, float]]:
        if self.text_index.count() == 0:
            return []
        vector = self.text_embedder.embed_texts([query])[0]
        result = self.text_index.query(query_embeddings=[vector], n_results=min(k, self.text_index.count()), where=self._where(doc_ids))
        hits = []
        for chunk_id, distance in zip(result["ids"][0], result["distances"][0]):
            chunk = self.get_chunk(chunk_id)
            if chunk:
                hits.append((chunk, 1.0 - float(distance)))
        return hits

    def image_search(self, query: str, k: int, doc_ids: list[str] | None = None) -> list[tuple[str, Page, float]]:
        if self.image_index.count() == 0:
            return []
        vector = self.image_embedder.embed_query(query)
        result = self.image_index.query(query_embeddings=[vector], n_results=min(k, self.image_index.count()), where=self._where(doc_ids))
        hits = []
        for page_key, distance in zip(result["ids"][0], result["distances"][0]):
            doc_id, number = page_key.split(":")
            page = self.get_page(doc_id, int(number))
            if page:
                hits.append((doc_id, page, 1.0 - float(distance)))
        return hits
