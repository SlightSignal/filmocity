"""Real filesystem links for platform safety checks; no global OS changes."""
import os
import unittest


def file_link(link, target):
    try:
        link.symlink_to(target)
    except OSError as error:
        if os.name == 'nt' and error.winerror == 1314:
            raise unittest.SkipTest('File symlink creation requires a Windows privilege; directory junction checks run separately') from error
        raise


def directory_link(link, target):
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        if not link.is_junction():
            raise AssertionError('Windows junction fixture was not created')
    else:
        link.symlink_to(target, target_is_directory=True)
