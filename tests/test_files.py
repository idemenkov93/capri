from pathlib import Path

import pytest

from capri import files


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Подменяем «известные папки» на временные, чтобы тесты ничего не трогали."""
    places = {k: tmp_path / k for k in ("desktop", "documents", "downloads", "pictures", "music", "videos", "project")}
    for p in places.values():
        p.mkdir()
    monkeypatch.setattr(files, "known_folder", lambda key: tmp_path if key == "home" else places[key])
    monkeypatch.setattr(files.Path, "home", staticmethod(lambda: tmp_path))
    return places


def test_mkdir_and_file(home):
    a = files.match("создай папку Проекты на рабочем столе")
    assert a.kind == "mkdir" and Path(a.params["path"]) == home["desktop"] / "Проекты"
    assert "Создала папку" in files.execute(a)
    assert (home["desktop"] / "Проекты").is_dir()
    f = files.match("создай текстовый документ план в документах с текстом купить хлеб")
    files.execute(f)
    assert (home["documents"] / "план.txt").read_text(encoding="utf-8").strip() == "купить хлеб"


def test_nested_and_default_place(home):
    (home["desktop"] / "Проекты").mkdir()
    a = files.match("создай папку test в папке Проекты на рабочем столе")
    assert Path(a.params["path"]) == home["desktop"] / "Проекты" / "test"
    assert Path(files.match("создай папку черновики").params["path"]) == home["desktop"] / "черновики"


def test_delete_goes_to_trash_with_confirmation(home):
    (home["downloads"] / "старое").mkdir()
    a = files.match("удали папку старое из загрузок")
    assert a.kind == "trash" and a.needs_confirm and "пустая" in a.description
    assert "не нашла" in files.match("удали папку нет-такой на рабочем столе")


def test_never_outside_safe_places(home, tmp_path):
    assert files.check_safe(Path("C:/Windows/System32")) is not None
    assert files.check_safe(home["desktop"], deleting=True) is not None    # саму папку «Рабочий стол» — нельзя
    assert files.check_safe(home["desktop"] / "x") is None


def test_open_known_folders_only_for_place_words():
    assert files.match_open("открой загрузки").kind == "open_folder"
    assert files.match_open("открой понравившейся музыка") is None
    assert files.match_open("открой ютуб мюзик") is None
