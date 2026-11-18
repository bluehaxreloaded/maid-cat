import os
import time

import pytest

import essential_store


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    folder = tmp_path / "essentials"
    monkeypatch.setattr(essential_store, "ESSENTIALS_DIR", folder)
    return folder


def test_save_and_load(store):
    essential_store.save_essential(111, 222, b"data")
    assert essential_store.load_essential(111, 222) == b"data"
    assert oct(store.stat().st_mode & 0o777) == "0o700"
    assert oct((store / "111-222.exefs").stat().st_mode & 0o777) == "0o600"


def test_only_the_helpee_gets_their_file():
    essential_store.save_essential(111, 222, b"data")
    assert essential_store.load_essential(111, 333) is None
    assert essential_store.load_essential(111, None) == b"data"  # channels with no helpee in the topic


def test_new_upload_replaces_the_old_one():
    essential_store.save_essential(111, 222, b"old")
    essential_store.save_essential(111, 333, b"new")
    assert essential_store.load_essential(111, 222) is None
    assert essential_store.load_essential(111, 333) == b"new"


def test_delete():
    essential_store.save_essential(111, 222, b"data")
    essential_store.delete_essential(111)
    assert essential_store.load_essential(111, 222) is None


def test_sweep_wipes_closed_and_old_files_only(store):
    essential_store.save_essential(1, 9, b"open")
    essential_store.save_essential(2, 9, b"closed")
    essential_store.save_essential(3, 9, b"old")
    old = time.time() - essential_store.MAX_AGE - 60
    os.utime(store / "3-9.exefs", (old, old))
    (store / "notes.txt").write_text("not ours")

    assert essential_store.sweep_essentials({1, 3}) == 2
    assert essential_store.load_essential(1, 9) == b"open"
    assert (store / "notes.txt").exists()


def test_refuses_a_symlinked_folder(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setattr(essential_store, "ESSENTIALS_DIR", link)
    with pytest.raises(RuntimeError):
        essential_store.save_essential(1, 2, b"data")
