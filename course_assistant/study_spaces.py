"""Local, single-user Study Spaces and safe migration of the old library."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class StudySpaceError(ValueError):
    """A safe, user-facing error concerning Study Spaces."""


@dataclass(frozen=True)
class StudySpace:
    space_id: str
    name: str


class StudySpaces:
    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.registry = self.root / "profiles.json"
        self.profiles_dir = self.root / "profiles"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        if self._is_link(self.profiles_dir):
            raise StudySpaceError("The Study Spaces directory cannot be a link.")
        if not self.registry.exists():
            self._initialize()
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        for item in data["spaces"]:
            self._validate_id(item["space_id"])
        self.spaces = {item["space_id"]: StudySpace(**item) for item in data["spaces"]}
        if len(self.spaces) != len(data["spaces"]):
            raise StudySpaceError("The Study Spaces registry contains duplicate IDs.")
        self.active_id = data["active_id"]
        if not self.spaces or self.active_id not in self.spaces:
            raise StudySpaceError("The Study Spaces registry is invalid; your data was not changed.")

    def path(self, space_id: str) -> Path:
        self._validate_id(space_id)
        if space_id not in self.spaces:
            raise StudySpaceError("That Study Space does not exist.")
        path = self.profiles_dir / space_id
        if not path.is_dir():
            raise StudySpaceError(
                f"Study Space {space_id} is missing from storage; no empty replacement was created. "
                "Inspect the profiles directory for an interrupted deletion before retrying."
            )
        if (self._is_link(self.profiles_dir) or self._is_link(path) or
                path.resolve().parent != self.profiles_dir.resolve() or
                any(self._is_link(item) for item in path.rglob("*"))):
            raise StudySpaceError("A Study Space path points outside its storage directory.")
        return path

    @staticmethod
    def _validate_id(space_id: str) -> None:
        if not isinstance(space_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", space_id):
            raise StudySpaceError("The Study Spaces registry contains an invalid ID.")

    @property
    def active(self) -> StudySpace:
        return self.spaces[self.active_id]

    def list(self) -> list[StudySpace]:
        return list(self.spaces.values())

    def _save(self) -> None:
        data = {"version": 1, "active_id": self.active_id,
                "spaces": [s.__dict__ for s in self.spaces.values()]}
        fd, name = tempfile.mkstemp(prefix=".profiles-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2)
            os.replace(name, self.registry)
        except Exception:
            try:
                Path(name).unlink(missing_ok=True)
            except OSError:
                pass  # preserve the original pre-commit error; the old registry remains
            raise

    def _initialize(self) -> None:
        """Stage a copy; publish a fresh default only after it is complete."""
        destination = self.profiles_dir / "default"
        if destination.exists() or self._is_link(destination):
            raise StudySpaceError(
                "The default Study Space already exists without a registry. Migration stopped without "
                "overwriting it; inspect profiles/default and profiles.json before retrying."
            )
        old_manifest = self.root / "library.json"
        sources = [self.root / name for name in ("docs", "chroma")]
        for source in [*sources, old_manifest]:
            if self._is_link(source) or (source.exists() and source.is_dir() and
                                         any(self._is_link(item) for item in source.rglob("*"))):
                raise StudySpaceError("A linked legacy path would expose outside files; migration stopped.")
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        staging = self.profiles_dir / f".migrating-default-{uuid.uuid4().hex}"
        staging.mkdir()
        for name in ("docs", "chroma"):
            source = self.root / name
            if source.exists():
                shutil.copytree(source, staging / name, symlinks=True)
        if old_manifest.exists():
            manifest = json.loads(old_manifest.read_text(encoding="utf-8"))
            source_docs = self.root / "docs"
            dest_docs = destination / "docs"
            for document in manifest.get("documents", []):
                for item in document.get("pages", []) + document.get("chunks", []):
                    image = item.get("image_path")
                    if image:
                        try:
                            relative = Path(image).resolve().relative_to(source_docs.resolve())
                        except ValueError as exc:
                            raise StudySpaceError("A legacy image path points outside the library; migration stopped.") from exc
                        item["image_path"] = str(dest_docs / relative)
            (staging / "library.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        if any(self._is_link(item) for item in staging.rglob("*")):
            raise StudySpaceError("A linked path appeared while copying; migration stopped.")
        # The original is untouched, even if the final registry write fails. A retry
        # refuses an existing destination instead of merging or overwriting its data.
        staging.rename(destination)
        self.spaces = {"default": StudySpace("default", "My Study Space")}
        self.active_id = "default"
        self._save()  # registry is the commit marker; original remains as backup

    def create(self, name: str, *, prepare: Callable[[str], object] | None = None,
               dispose: Callable[[object], None] | None = None) -> StudySpace | tuple[StudySpace, object]:
        name = " ".join(name.split())
        if not name or len(name) > 80:
            raise StudySpaceError("Enter a Study Space name of 1–80 characters.")
        with self._lock:
            if self._is_link(self.profiles_dir):
                raise StudySpaceError("The Study Spaces directory cannot be a link.")
            if any(s.name.casefold() == name.casefold() for s in self.spaces.values()):
                raise StudySpaceError("A Study Space with that name already exists.")
            stem = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "space"
            space_id = f"{stem}-{uuid.uuid4().hex[:8]}"
            space = StudySpace(space_id, name)
            path = self.profiles_dir / space_id
            path.mkdir(parents=True)
            self.spaces[space_id] = space
            previous_active = self.active_id
            prepared = None
            try:
                if prepare is not None:
                    prepared = prepare(space_id)
                    self.active_id = space_id
                self._save()
            except Exception:
                self.active_id = previous_active
                del self.spaces[space_id]
                if prepared is not None and dispose is not None:
                    dispose(prepared)
                shutil.rmtree(path)  # no committed registry entry points at this directory
                raise
            return (space, prepared) if prepare is not None else space

    def switch(self, space_id: str) -> None:
        with self._lock:
            self.path(space_id)
            previous = self.active_id
            self.active_id = space_id
            try:
                self._save()
            except Exception:
                self.active_id = previous
                raise

    def rename(self, space_id: str, name: str) -> StudySpace:
        name = " ".join(name.split())
        if not name or len(name) > 80:
            raise StudySpaceError("Enter a Study Space name of 1–80 characters.")
        with self._lock:
            self.path(space_id)
            if any(s.space_id != space_id and s.name.casefold() == name.casefold() for s in self.spaces.values()):
                raise StudySpaceError("A Study Space with that name already exists.")
            space = StudySpace(space_id, name)
            previous = self.spaces[space_id]
            self.spaces[space_id] = space
            try:
                self._save()
            except Exception:
                self.spaces[space_id] = previous
                raise
            return space

    def delete(self, space_id: str) -> None:
        with self._lock:
            path = self.path(space_id)
            if space_id == self.active_id:
                raise StudySpaceError("Switch to another Study Space before deleting this one.")
            if len(self.spaces) == 1:
                raise StudySpaceError("You cannot delete the last Study Space.")
            # Quarantine first. Files are not destroyed until the registry commit succeeds.
            pending = self.profiles_dir / f".deleting-{uuid.uuid4().hex}"
            path.rename(pending)
            space = self.spaces.pop(space_id)
            try:
                self._save()
            except Exception:
                self.spaces[space_id] = space
                try:
                    pending.rename(path)
                except OSError as exc:
                    raise StudySpaceError(
                        f"Registry update failed; files are safe at {pending}. Restore them to {path}."
                    ) from exc
                raise
            try:
                shutil.rmtree(pending)
            except OSError as exc:
                raise StudySpaceError(
                    f"Study Space was removed from the registry, but its files could not be fully deleted at {pending}."
                ) from exc
