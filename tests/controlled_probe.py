"""Run injected probe faults as real portable children without POSIX shebangs.

Only the named fixture executable is substituted. Production probe arguments,
pipe/file handles, creation flags and cancellation ownership remain intact.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


@contextmanager
def python_probe(script, source):
    script = Path(script)
    script.write_text(source, encoding='utf-8')
    original = subprocess.Popen

    def launch(args, *positional, **options):
        if isinstance(args, (list, tuple)) and args and os.fspath(args[0]) == str(script):
            # A Windows venv executable redirects to a second Python process.
            # ffprobe is a direct binary, so its injected replacement must own
            # the metadata writer rather than an intermediary waiting for it.
            interpreter = sys._base_executable if os.name == 'nt' else sys.executable
            args = [interpreter, str(script), *args[1:]]
        return original(args, *positional, **options)

    with patch.object(subprocess, 'Popen', launch):
        yield str(script)
