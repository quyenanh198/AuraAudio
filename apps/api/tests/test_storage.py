import pytest

from aura_api.storage import LocalStorageClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config

    monkeypatch.setattr(config, "settings", config.Settings())
    import aura_api.storage as storage_module

    monkeypatch.setattr(storage_module, "settings", config.settings)
    return LocalStorageClient()


def test_put_then_get_bytes_round_trips(client):
    client.put_bytes("jobs/1/a.json", b"hello")
    assert client.get_bytes("jobs/1/a.json") == b"hello"


def test_put_bytes_creates_parent_directories(client, tmp_path):
    client.put_bytes("a/b/c/d.bin", b"x")
    assert (tmp_path / "blobs" / "a" / "b" / "c" / "d.bin").is_file()


def test_download_media_asset_copies_to_dest(client, tmp_path):
    client.put_bytes("uploads/x/riff.wav", b"audio-bytes")
    dest = tmp_path / "work" / "input"
    result = client.download_media_asset("uploads/x/riff.wav", dest)
    assert result == dest
    assert dest.read_bytes() == b"audio-bytes"


def test_head_object_returns_content_length_for_existing_key(client):
    client.put_bytes("uploads/x/riff.wav", b"12345")
    head = client.head_object("uploads/x/riff.wav")
    assert head == {"ContentLength": 5}


def test_head_object_returns_none_for_missing_key(client):
    assert client.head_object("does/not/exist") is None


def test_path_for_returns_filesystem_path_under_blob_root(client, tmp_path):
    assert client.path_for("a/b.mid") == tmp_path / "blobs" / "a" / "b.mid"


def test_path_for_resolves_normal_nested_key(client, tmp_path):
    # Regression check: the containment fix must not break ordinary nested
    # keys that stay under the blob root.
    assert client.path_for("a/b/c.txt") == (tmp_path / "blobs" / "a" / "b" / "c.txt").resolve()


def test_path_for_rejects_key_that_escapes_root(client):
    with pytest.raises(ValueError):
        client.path_for("../outside")


def test_path_for_rejects_absolute_key_that_escapes_root(client):
    with pytest.raises(ValueError):
        client.path_for("/etc/passwd")
