import os
import time
from pathlib import Path
import constants

# Helpees' essential.exefs files, kept outside the repo so they never end up in git or a Discord channel.
# Only the bot's own user can open the folder or the files in it.
ESSENTIALS_DIR = Path(getattr(constants, "ESSENTIALS_DIR", None) or "~/essentials").expanduser()
MAX_AGE = 7 * 24 * 60 * 60  # seconds; anything older is wiped even if its channel is still open


def _folder() -> Path:
    if ESSENTIALS_DIR.is_symlink():
        raise RuntimeError(f"{ESSENTIALS_DIR} is a symlink, refusing to store essentials there")
    ESSENTIALS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ESSENTIALS_DIR, 0o700)
    return ESSENTIALS_DIR


def _path(channel_id: int, user_id: int) -> Path:
    # The name only ever comes from the channel and helpee IDs, never from the uploaded file.
    # Having the helpee in the name means a file is only found for the person it belongs to.
    return _folder() / f"{int(channel_id)}-{int(user_id)}.exefs"


def _channel_files(channel_id: int) -> list[Path]:
    return list(_folder().glob(f"{int(channel_id)}-*.exefs"))


def _ids(path: Path) -> tuple[int, int] | None:
    """(channel ID, helpee ID) from one of our file names, or None if it isn't one."""
    channel_id, _, user_id = path.stem.partition("-")
    if path.suffix not in (".exefs", ".tmp") or not channel_id.isdigit() or not user_id.isdigit():
        return None
    return int(channel_id), int(user_id)


def save_essential(channel_id: int, user_id: int, data: bytes):
    """Store a helpee's essential.exefs for their channel, replacing any older one. Only pass a rebuilt, fully checked file."""
    path = _path(channel_id, user_id)
    for old in _channel_files(channel_id):
        if old != path:
            old.unlink(missing_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def load_essential(channel_id: int, user_id: int | None) -> bytes | None:
    """The essential.exefs stored for this helpee in this channel, if there is one."""
    if user_id is None:
        return None
    try:
        fd = os.open(_path(channel_id, user_id), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as f:
        return f.read()


def delete_essential(channel_id: int):
    """Wipe a channel's essential.exefs."""
    for path in _channel_files(channel_id):
        path.unlink(missing_ok=True)


def sweep_essentials(open_channel_ids: set[int]) -> int:
    """Wipe essentials whose channel is gone or that are older than MAX_AGE. Returns how many were wiped."""
    wiped = 0
    cutoff = time.time() - MAX_AGE
    for path in _folder().iterdir():
        ids = _ids(path)
        # Only touch files this module made, in case the folder is ever pointed somewhere else
        if ids is None:
            continue
        if path.suffix == ".exefs" and ids[0] in open_channel_ids and path.lstat().st_mtime > cutoff:
            continue
        path.unlink(missing_ok=True)
        wiped += 1
    return wiped
