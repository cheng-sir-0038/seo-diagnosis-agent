from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from src.core import config


@pytest.fixture
def config_from_other_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    source = project / "src/core/config.py"
    source.parent.mkdir(parents=True)
    shutil.copyfile(Path(config.__file__), source)
    (project / ".env").write_text("PAGESPEED_API_KEY=base-fixture\n")
    (project / ".env.local").write_text("PAGESPEED_API_KEY=local-fixture\n")
    working = tmp_path / "another-directory"
    working.mkdir()
    (working / ".env.local").write_text("PAGESPEED_API_KEY=wrong-directory\n")
    monkeypatch.chdir(working)
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    spec = importlib.util.spec_from_file_location("isolated_core_config", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_default_env_files_are_loaded_from_project_not_working_directory(
    config_from_other_directory,
):
    assert config_from_other_directory.settings.pagespeed_api_key == "local-fixture"


def test_environment_still_overrides_project_local_file(config_from_other_directory, monkeypatch):
    monkeypatch.setenv("PAGESPEED_API_KEY", "environment-fixture")
    assert config_from_other_directory.Settings().pagespeed_api_key == "environment-fixture"
