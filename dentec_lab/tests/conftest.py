import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dentec.config import Paths, set_paths  # noqa: E402
from dentec.db import database  # noqa: E402
from dentec.services.common import CurrentUser, set_current_user  # noqa: E402


@pytest.fixture()
def home(tmp_path):
    paths = set_paths(Paths(tmp_path / "DentecLab"))
    database.init_db(paths.db_file)
    set_current_user(CurrentUser(id=1, username="admin", role="admin"))
    yield paths
    database.close_db()


@pytest.fixture()
def s(home):
    sess = database.new_session()
    yield sess
    sess.rollback()
    sess.close()



_EXIT = {"status": 0}


def pytest_sessionfinish(session, exitstatus):
    _EXIT["status"] = int(exitstatus)


def pytest_unconfigure(config):
    """Exit immediately after the summary is printed.  Qt objects destroyed during
    normal interpreter shutdown can crash on Windows and turn a green run red."""
    import os
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        from dentec.hard_exit import hard_exit
        hard_exit(_EXIT["status"])
