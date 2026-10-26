from capri.ui.sprites import classify


def test_classify():
    assert classify("walk_left") == "walk"
    assert classify("capri-sitting2") == "sit"
    assert classify("Talking") == "talk"
    assert classify("output") is None          # «tp» внутри слова не считается
    assert classify("capri_idle_03") == "idle"
    assert classify("random_dance") is None
