import json

from recorder.chapters import TimelinePoint, build_chapters, read_events_file, to_ffmetadata


def test_chapters_split_on_category_only():
    timeline = [
        TimelinePoint(0, "26936", "Music", "Singing"),
        TimelinePoint(60_000, "26936", "Music", "Singing, requests open"),  # title only: same chapter
        TimelinePoint(3_600_000, "516575", "VALORANT", "Ranked"),
    ]
    chapters = build_chapters(timeline, duration_ms=5_000_000)
    assert [(c["title"], c["start_ms"], c["end_ms"]) for c in chapters] == [
        ("Music", 0, 3_600_000),
        ("VALORANT", 3_600_000, 5_000_000),
    ]


def test_ffmetadata_escapes_special_characters():
    text = to_ffmetadata([{"start_ms": 0, "end_ms": 10, "title": "A=B;#C\\"}])
    assert text.splitlines() == [";FFMETADATA1", "[CHAPTER]", "TIMEBASE=1/1000", "START=0", "END=10",
                                 "title=A\\=B\\;\\#C\\\\"]


def test_read_events_file_skips_torn_lines(tmp_path):
    path = tmp_path / "x.events.jsonl"
    lines = [
        {"event": "recording.started", "recording.offset_ms": 0, "category.id": "1", "category.name": "Music",
         "stream.id": "111"},
        {"event": "stream.metadata_changed", "recording.offset_ms": 5000, "category.id": "2",
         "category.name": "VALORANT"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + '\n{"event": "recording.sess')
    timeline, started = read_events_file(str(path))
    assert [p.category_name for p in timeline] == ["Music", "VALORANT"]
    assert started["stream.id"] == "111"
