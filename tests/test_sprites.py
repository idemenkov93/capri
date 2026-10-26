from capri.ui.sprites import classify


def test_classify():
    assert classify("walk_left") == "walk"
    assert classify("capri-sitting2") == "sit"
    assert classify("Talking") == "talk"
    assert classify("output") is None          # «tp» внутри слова не считается
    assert classify("capri_idle_03") == "idle"
    assert classify("random_dance") is None


def test_frame_folder_is_one_animation(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication, QImage
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    from capri.ui.sprites import SpriteSet, frame_folder

    walk = tmp_path / "walk"
    walk.mkdir()
    for i in range(4):
        img = QImage(8, 8, QImage.Format_ARGB32)
        img.fill(0xFF00FF00 + i)
        img.save(str(walk / f"{i:04d}.png"))
    img.save(str(tmp_path / "idle.png"))
    assert frame_folder(walk) and len(frame_folder(walk)) == 4
    s = SpriteSet(str(tmp_path))
    assert s.kinds() == {"walk": 1, "idle": 1}          # 4 кадра = одна анимация, а не 4 картинки
    assert not s.get("walk").frame().isNull()
