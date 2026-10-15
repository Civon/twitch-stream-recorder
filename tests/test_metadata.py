import dataclasses

from recorder.metadata import StreamMetadata, diff

from helpers import stream


def test_from_helix_maps_game_to_category():
    meta = StreamMetadata.from_helix({
        "id": "123", "user_id": "42", "user_login": "Kana", "game_id": "26936", "game_name": "Music",
        "title": "歌枠", "tags": None, "language": "ja", "is_mature": False, "viewer_count": 5,
        "started_at": "2026-10-15T08:00:00Z",
    })
    assert meta.user_login == "kana"
    assert (meta.category_id, meta.category_name) == ("26936", "Music")
    assert meta.tags == ()


def test_no_change_when_only_viewers_differ():
    assert diff(stream(), dataclasses.replace(stream(), viewer_count=999)) is None


def test_category_change_from_singing_to_game():
    before = stream()
    after = stream(category=("516575", "VALORANT"), title="Ranked with friends", tags=("FPS", "English"))
    change = diff(before, after)
    assert change.fields == ("category", "title", "tags")
    fields = change.event_fields()
    assert fields["category.before.name"] == "Music"
    assert fields["category.name"] == "VALORANT"
    assert fields["stream.tags.added"] == ["FPS"]
    assert fields["stream.tags.removed"] == ["Singing"]
    assert fields["stream.title.before"] == "Singing stream"
    assert "category Music -> VALORANT" in change.summary()


def test_tag_reorder_is_not_a_change():
    assert diff(stream(tags=("a", "b")), stream(tags=("b", "a"))) is None


def test_category_compared_by_name_when_ids_missing():
    change = diff(stream(category=("", "Music")), stream(category=("", "Just Chatting")))
    assert change.fields == ("category",)
