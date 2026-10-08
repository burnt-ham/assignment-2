"""Study Spaces keep complete document/search contexts separate."""

from pathlib import Path
import json
import os
import threading

import pytest

from course_assistant.answering import NOT_FOUND
from course_assistant.library import Library
from course_assistant.services import HashingTextEmbedder, PageTextImageEmbedder
from course_assistant.assistant import CourseAssistant
from course_assistant.config import Settings
from conftest import offline_services
from app import build_app
from course_assistant.study_spaces import StudySpaceError


def test_switching_spaces_isolates_uploads_search_and_quizzes(make_assistant, sample_md):
    assistant = make_assistant()
    first = assistant.library.add_file(sample_md).document
    other = assistant.create_study_space("Finance Final")
    assert other.name == "Finance Final"
    assert assistant.library.list_documents() == []
    assert assistant.answerer.ask("When are office hours?").answer == NOT_FOUND
    assistant.library.add_file(sample_md)  # duplicates are local to a space
    assistant.switch_study_space("default")
    assert [d.doc_id for d in assistant.library.list_documents()] == [first.doc_id]
    assert assistant.answerer.ask("When are office hours?").found
    assert assistant.quiz_maker.library is assistant.library
    assistant.library.remove(first.doc_id)
    assistant.switch_study_space(other.space_id)
    assert len(assistant.library.list_documents()) == 1


def test_legacy_library_migrates_with_images_and_survives_restart(tmp_path, sample_pdf):
    root = tmp_path / "data"
    old = Library(root, HashingTextEmbedder(), PageTextImageEmbedder())
    document = old.add_file(sample_pdf).document
    original = old.get_page(document.doc_id, 1).image_path
    assistant = CourseAssistant(Settings(data_dir=root), offline_services())
    page = assistant.library.get_page(document.doc_id, 1)
    assert page.image_path != original
    assert page.image_path.startswith(str(root / "profiles" / "default"))
    assert Path(page.image_path).exists()
    assert assistant.library.keyword_search("reranking", 3)
    assert assistant.library.image_index.count() == 3
    assert Path(original).exists()  # original retained for recovery
    reopened = CourseAssistant(Settings(data_dir=root), offline_services())
    assert reopened.library.get_page(document.doc_id, 1).image_path == page.image_path


def test_interface_exposes_study_space_controls(make_assistant):
    app = build_app(make_assistant())
    labels = {component.get("props", {}).get("label") for component in app.config["components"]}
    assert {"Study Space", "New Study Space", "Rename Study Space", "Delete Study Space"} <= labels


def test_every_material_and_question_action_binds_selected_space(make_assistant):
    app = build_app(make_assistant())
    components = {c["id"]: c for c in app.config["components"]}
    space_id = next(i for i, c in components.items() if c["props"].get("label") == "Study Space")
    actions = {"Add to library", "Remove", "Ask", "Make quiz"}
    callbacks = [d for d in app.config["dependencies"]
                 if any(components[t[0]]["props"].get("value") in actions
                        for t in d["targets"] if t[0] in components)]
    assert len(callbacks) == 4
    assert all(space_id in d["inputs"] for d in callbacks)
    assert len({d.get("concurrency_id") for d in callbacks}) == 1


def test_rename_and_delete_bind_to_visible_space(make_assistant):
    app = build_app(make_assistant())
    components = {c["id"]: c for c in app.config["components"]}
    space_id = next(i for i, c in components.items() if c["props"].get("label") == "Study Space")
    callbacks = [d for d in app.config["dependencies"]
                 if any(components[t[0]]["props"].get("value") in {"Rename", "Delete permanently"}
                        for t in d["targets"] if t[0] in components)]
    assert len(callbacks) == 2
    assert all(space_id in d["inputs"] for d in callbacks)


def test_switch_resets_delete_confirmation(make_assistant):
    assistant = make_assistant()
    assistant.create_study_space("Finance Final")
    app = build_app(assistant)
    components = {c["id"]: c for c in app.config["components"]}
    space_id = next(i for i, c in components.items() if c["props"].get("label") == "Study Space")
    confirm_id = next(i for i, c in components.items() if c["props"].get("label") == "Delete Study Space")
    callback = next(d for d in app.config["dependencies"] if (space_id, "input") in d["targets"])
    assert confirm_id in callback["outputs"]
    result = app.fns[callback["id"]].fn("default")
    assert result[callback["outputs"].index(confirm_id)] is False


def test_failed_delete_does_not_switch_active_space(make_assistant, monkeypatch):
    assistant = make_assistant()
    active = assistant.create_study_space("Finance Final")
    app = build_app(assistant)
    components = {c["id"]: c for c in app.config["components"]}
    callback = next(d for d in app.config["dependencies"]
                    if any(components[t[0]]["props"].get("value") == "Delete permanently"
                           for t in d["targets"] if t[0] in components))

    def deletion_fails(_space_id):
        raise OSError("disk full")

    monkeypatch.setattr(assistant, "delete_study_space", deletion_fails)
    values = {"Delete Study Space": True, "Study Space": active.space_id,
              "Study Space to delete": "default"}
    args = [values[components[i]["props"]["label"]] for i in callback["inputs"]]
    app.fns[callback["id"]].fn(*args)
    assert assistant.study_spaces.active_id == active.space_id
    assert "default" in assistant.study_spaces.spaces


def test_stale_remove_callback_cannot_delete_identical_file_in_new_space(make_assistant, sample_md):
    assistant = make_assistant()
    old_doc = assistant.library.add_file(sample_md).document
    assistant.create_study_space("Finance Final")
    assistant.library.add_file(sample_md)
    app = build_app(assistant)
    components = {c["id"]: c for c in app.config["components"]}
    callback = next(d for d in app.config["dependencies"]
                    if any(components[t[0]]["props"].get("value") == "Remove"
                           for t in d["targets"] if t[0] in components))
    result = app.fns[callback["id"]].fn(old_doc.doc_id, "default")
    assert "Study Space changed" in result[0]
    assert len(assistant.library.list_documents()) == 1


def test_delete_only_removes_selected_space_and_rejects_active(make_assistant, sample_md):
    assistant = make_assistant()
    other = assistant.create_study_space("Finance Final")
    assistant.library.add_file(sample_md)
    path = assistant.study_spaces.path(other.space_id)
    with pytest.raises(StudySpaceError):
        assistant.delete_study_space(other.space_id)
    assistant.switch_study_space("default")
    assistant.delete_study_space(other.space_id)
    assert not path.exists()
    assert assistant.study_spaces.path("default").exists()
    with pytest.raises(StudySpaceError):
        assistant.delete_study_space("default")


def test_rename_keeps_documents_and_active_selection_after_restart(make_assistant, sample_md):
    assistant = make_assistant()
    space = assistant.create_study_space("Finance Final")
    assistant.library.add_file(sample_md)
    renamed = assistant.rename_study_space(space.space_id, "Finance Exam")
    assert renamed.space_id == space.space_id
    with pytest.raises(StudySpaceError):
        assistant.create_study_space("FINANCE EXAM")
    reopened = make_assistant()
    assert reopened.study_spaces.active.name == "Finance Exam"
    assert len(reopened.library.list_documents()) == 1
    assert reopened.library.keyword_search("office hours", 2)


def test_crafted_registry_id_cannot_escape_profiles(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "profiles.json").write_text(json.dumps({
        "version": 1, "active_id": "default",
        "spaces": [{"space_id": "default", "name": "Safe"},
                   {"space_id": "../../outside", "name": "Unsafe"}],
    }), encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("do not delete", encoding="utf-8")
    with pytest.raises(StudySpaceError):
        CourseAssistant(Settings(data_dir=root), offline_services())
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "do not delete"


def test_migration_refuses_symlinked_default_destination(tmp_path, sample_md):
    root = tmp_path / "data"
    root.mkdir()
    (root / "library.json").write_text('{"documents": []}', encoding="utf-8")
    destination = tmp_path / "outside"
    destination.mkdir()
    (root / "profiles").mkdir()
    try:
        (root / "profiles" / "default").symlink_to(destination, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this machine")
    with pytest.raises(StudySpaceError):
        CourseAssistant(Settings(data_dir=root), offline_services())
    assert not (destination / "library.json").exists()
    assert not (root / "profiles.json").exists()


def test_migration_checks_destination_before_writing(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    (root / "library.json").write_text('{"documents": []}', encoding="utf-8")
    original = Path.is_symlink
    suspect = root / "profiles" / "default"
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == suspect or original(path))
    with pytest.raises(StudySpaceError):
        CourseAssistant(Settings(data_dir=root), offline_services())
    assert not (root / "profiles.json").exists()


def test_migration_refuses_linked_child_directory(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "sample.txt").write_text("source", encoding="utf-8")
    (root / "profiles" / "default" / "docs").mkdir(parents=True)
    original = Path.is_symlink
    suspect = root / "profiles" / "default" / "docs"
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == suspect or original(path))
    with pytest.raises(StudySpaceError):
        CourseAssistant(Settings(data_dir=root), offline_services())
    assert not (root / "profiles.json").exists()


def test_migration_refuses_linked_legacy_child(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "docs").mkdir(parents=True)
    source_child = root / "docs" / "external.txt"
    source_child.write_text("private", encoding="utf-8")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == source_child or original(path))
    with pytest.raises(StudySpaceError):
        CourseAssistant(Settings(data_dir=root), offline_services())
    assert not (root / "profiles.json").exists()
    assert not (root / "profiles" / "default").exists()
    assert source_child.read_text(encoding="utf-8") == "private"


def test_migration_retry_never_overwrites_existing_default(tmp_path):
    root = tmp_path / "data"
    (root / "docs").mkdir(parents=True)
    (root / "library.json").write_text('{"documents": []}', encoding="utf-8")
    existing = root / "profiles" / "default"
    existing.mkdir(parents=True)
    manifest = existing / "library.json"
    manifest.write_text('{"documents": [{"name": "keep"}]}', encoding="utf-8")
    with pytest.raises(StudySpaceError):
        CourseAssistant(Settings(data_dir=root), offline_services())
    assert json.loads(manifest.read_text(encoding="utf-8"))["documents"] == [{"name": "keep"}]
    assert not (root / "profiles.json").exists()


def test_failed_migration_commit_preserves_original_and_destination(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "docs").mkdir(parents=True)
    original = root / "docs" / "sample.txt"
    original.write_text("original", encoding="utf-8")
    (root / "library.json").write_text('{"documents": []}', encoding="utf-8")

    def disk_full(_self):
        raise OSError("disk full")

    from course_assistant.study_spaces import StudySpaces
    monkeypatch.setattr(StudySpaces, "_save", disk_full)
    with pytest.raises(OSError, match="disk full"):
        CourseAssistant(Settings(data_dir=root), offline_services())
    assert original.read_text(encoding="utf-8") == "original"
    assert (root / "profiles" / "default" / "docs" / "sample.txt").read_text(encoding="utf-8") == "original"
    assert not (root / "profiles.json").exists()
    with pytest.raises(StudySpaceError, match="already exists"):
        CourseAssistant(Settings(data_dir=root), offline_services())


def test_registry_save_does_not_overwrite_hardlinked_temp_target(tmp_path):
    from course_assistant.study_spaces import StudySpaces
    registry = StudySpaces(tmp_path / "data")
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    os.link(outside, registry.registry.with_suffix(".tmp"))
    registry.rename("default", "Updated")
    assert outside.read_text(encoding="utf-8") == "private"
    assert json.loads(registry.registry.read_text(encoding="utf-8"))["spaces"][0]["name"] == "Updated"


def test_post_commit_temp_cleanup_cannot_undo_create(tmp_path, monkeypatch):
    from course_assistant.study_spaces import StudySpaces
    registry = StudySpaces(tmp_path / "data")
    original = Path.unlink

    def cleanup_fails(path, *args, **kwargs):
        if path.name.startswith(".profiles-"):
            raise OSError("cleanup failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", cleanup_fails)
    created = registry.create("Finance Final")
    assert registry.path(created.space_id).is_dir()
    assert created.space_id in {item["space_id"] for item in json.loads(
        registry.registry.read_text(encoding="utf-8"))["spaces"]}


def test_interrupted_deletion_does_not_recreate_empty_library(make_assistant, sample_md):
    assistant = make_assistant()
    created = assistant.create_study_space("Finance Final")
    assistant.library.add_file(sample_md)
    assistant.switch_study_space("default")
    path = assistant.study_spaces.path(created.space_id)
    pending = path.with_name(".deleting-interrupted")
    path.rename(pending)
    reopened = make_assistant()
    with pytest.raises(StudySpaceError, match="missing"):
        reopened.switch_study_space(created.space_id)
    assert not path.exists()
    assert (pending / "library.json").exists()


def test_linked_space_storage_child_is_rejected(make_assistant, monkeypatch):
    assistant = make_assistant()
    path = assistant.study_spaces.path("default")
    original = Path.is_symlink
    suspect = path / "docs"
    monkeypatch.setattr(Path, "is_symlink", lambda item: item == suspect or original(item))
    with pytest.raises(StudySpaceError):
        assistant.study_spaces.path("default")


def test_create_refuses_linked_profiles_directory(make_assistant, monkeypatch):
    assistant = make_assistant()
    original = Path.is_symlink
    suspect = assistant.study_spaces.profiles_dir
    monkeypatch.setattr(Path, "is_symlink", lambda item: item == suspect or original(item))
    with pytest.raises(StudySpaceError):
        assistant.study_spaces.create("Finance Final")


def test_failed_registry_save_does_not_change_live_library(make_assistant, sample_md, monkeypatch):
    assistant = make_assistant()
    original = assistant.library.add_file(sample_md).document
    other = assistant.study_spaces.create("Finance Final")

    def disk_full():
        raise OSError("disk full")

    monkeypatch.setattr(assistant.study_spaces, "_save", disk_full)
    with pytest.raises(OSError, match="disk full"):
        assistant.switch_study_space(other.space_id)
    assert assistant.study_spaces.active_id == "default"
    assert assistant.library.data_dir == assistant.study_spaces.path("default")
    assert assistant.library.get_chunk(original.chunks[0].chunk_id) is not None


def test_failed_new_space_open_does_not_register_it(make_assistant, monkeypatch):
    assistant = make_assistant()
    original = assistant._build_space

    def cannot_open(space_id):
        if space_id != "default":
            raise OSError("cannot open library")
        return original(space_id)

    monkeypatch.setattr(assistant, "_build_space", cannot_open)
    with pytest.raises(OSError, match="cannot open library"):
        assistant.create_study_space("Finance Final")
    assert [s.space_id for s in assistant.study_spaces.list()] == ["default"]
    assert assistant.study_spaces.active_id == "default"
    assert [p.name for p in assistant.study_spaces.profiles_dir.iterdir()] == ["default"]


def test_failed_new_space_registry_save_leaves_old_library_active(make_assistant, monkeypatch):
    assistant = make_assistant()
    old_library = assistant.library

    def disk_full():
        raise OSError("disk full")

    monkeypatch.setattr(assistant.study_spaces, "_save", disk_full)
    with pytest.raises(OSError, match="disk full"):
        assistant.create_study_space("Finance Final")
    assert assistant.library is old_library
    assert assistant.study_spaces.active_id == "default"
    assert [s.space_id for s in assistant.study_spaces.list()] == ["default"]
    assert [p.name for p in assistant.study_spaces.profiles_dir.iterdir()] == ["default"]


def test_failed_create_does_not_publish_a_space(make_assistant, monkeypatch):
    assistant = make_assistant()
    root = assistant.settings.data_dir

    def disk_full():
        raise OSError("disk full")

    monkeypatch.setattr(assistant.study_spaces, "_save", disk_full)
    with pytest.raises(OSError, match="disk full"):
        assistant.create_study_space("Finance Final")
    assert [s.space_id for s in assistant.study_spaces.list()] == ["default"]
    assert [p.name for p in (root / "profiles").iterdir()] == ["default"]
    assert assistant.study_spaces.active_id == "default"


def test_failed_rename_keeps_old_name(make_assistant, monkeypatch):
    assistant = make_assistant()
    space = assistant.study_spaces.create("Finance Final")

    def disk_full():
        raise OSError("disk full")

    monkeypatch.setattr(assistant.study_spaces, "_save", disk_full)
    with pytest.raises(OSError, match="disk full"):
        assistant.rename_study_space(space.space_id, "Other Name")
    assert assistant.study_spaces.spaces[space.space_id].name == "Finance Final"


def test_failed_delete_preserves_document_and_registry(make_assistant, sample_md, monkeypatch):
    assistant = make_assistant()
    other = assistant.create_study_space("Finance Final")
    document = assistant.library.add_file(sample_md).document
    assistant.switch_study_space("default")
    registry = assistant.study_spaces
    old_save = registry._save

    def disk_full():
        raise OSError("disk full")

    monkeypatch.setattr(registry, "_save", disk_full)
    with pytest.raises(OSError, match="disk full"):
        assistant.delete_study_space(other.space_id)
    assert other.space_id in registry.spaces
    assert registry.path(other.space_id).exists()
    monkeypatch.setattr(registry, "_save", old_save)
    assistant.switch_study_space(other.space_id)
    assert document.doc_id in {item.doc_id for item in assistant.library.list_documents()}


def test_stale_request_cannot_modify_another_space(make_assistant, sample_md):
    assistant = make_assistant()
    original = assistant.library.add_file(sample_md).document
    other = assistant.create_study_space("Finance Final")
    assistant.library.add_file(sample_md)  # same hash => same ID in both spaces
    with pytest.raises(StudySpaceError):
        with assistant.operation("default"):
            assistant.library.remove(original.doc_id)
    assert len(assistant.library.list_documents()) == 1
    assert assistant.study_spaces.active_id == other.space_id


def test_switch_waits_for_in_flight_operation(make_assistant):
    assistant = make_assistant()
    other = assistant.create_study_space("Finance Final")
    assistant.switch_study_space("default")
    started, release, switched = threading.Event(), threading.Event(), threading.Event()

    def reading():
        with assistant.operation("default"):
            started.set()
            assert release.wait(5)
            assert assistant.study_spaces.active_id == "default"

    def changing():
        assistant.switch_study_space(other.space_id)
        switched.set()

    first = threading.Thread(target=reading)
    second = threading.Thread(target=changing)
    first.start()
    assert started.wait(5)
    second.start()
    try:
        assert not switched.wait(0.1)
    finally:
        release.set()
        first.join(5)
        second.join(5)
    assert switched.is_set()
    assert assistant.study_spaces.active_id == other.space_id
