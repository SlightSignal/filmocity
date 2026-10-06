"""Cache path checks, including Python 3.11 Windows reparse attributes."""
from pathlib import Path
import stat


def linked(path, info=None):
    path=Path(path)
    if path.is_symlink() or getattr(path,'is_junction',lambda:False)(): return True
    try: info=info if info is not None else path.lstat()
    except FileNotFoundError: return False
    # Python 3.11 on Windows already exposes the reparse attribute; is_junction
    # is newer. Refuse every reparse entry rather than follow an unknown target.
    return bool(getattr(info,'st_file_attributes',0) & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400))
