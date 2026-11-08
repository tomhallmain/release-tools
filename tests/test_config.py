"""app_release.config: where configuration is read from, and its merging."""

import pytest

from app_release import config


def test_no_file_means_the_defaults(tmp_path):
    assert config.load(str(tmp_path)) == ({}, None)


def test_release_toml(tmp_path):
    (tmp_path / "release.toml").write_text('builder = "cargo"\n', encoding="utf-8")
    loaded, path = config.load(str(tmp_path))
    assert loaded == {"builder": "cargo"} and path.endswith("release.toml")


def test_pyproject_tool_release(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n\n[tool.release]\nbuilder = "command"\n',
                                             encoding="utf-8")
    loaded, path = config.load(str(tmp_path))
    assert loaded == {"builder": "command"} and path.endswith("pyproject.toml")


def test_a_pyproject_without_the_table_is_ignored(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (tmp_path / "release.toml").write_text('builder = "cargo"\n', encoding="utf-8")
    assert config.load(str(tmp_path))[0] == {"builder": "cargo"}


def test_both_places_are_refused(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.release]\nbuilder = "command"\n', encoding="utf-8")
    (tmp_path / "release.toml").write_text('builder = "cargo"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        config.load(str(tmp_path))


def test_invalid_toml_is_refused(tmp_path):
    (tmp_path / "release.toml").write_text("builder = \n", encoding="utf-8")
    with pytest.raises(SystemExit):
        config.load(str(tmp_path))


def test_merged_overrides_and_removes():
    defaults = {"oqs": {"note": "a"}, "torch": {"variant": "cuda"}}
    assert config.merged(defaults, {"oqs": False, "x": {"note": "b"}}) == {
        "torch": {"variant": "cuda"}, "x": {"note": "b"}}
    assert config.merged(defaults, None) == defaults


def test_unknown_keys():
    assert config.unknown_keys({"builder": "x", "cargo": {}, "biulder": "y"}, {"cargo"}) == ["biulder"]


@pytest.mark.parametrize("products, name", [
    (["Weidr", "WeidrHeadless"], "Weidr"),
    (["WeidrHeadless"], "Weidr"),
    (["tool"], "tool"),
])
def test_app_name(products, name):
    assert config.app_name({}, products) == name


def test_platforms_default_to_none_and_must_be_a_list_of_names():
    assert config.platforms({}) == []
    assert config.platforms({"platforms": ["linux-x64"]}) == ["linux-x64"]
    assert "platforms" not in config.unknown_keys({"platforms": []}, set())
    for bad in ("linux-x64", [""], [1]):
        with pytest.raises(SystemExit):
            config.platforms({"platforms": bad})


def test_archiving_unix_executables_is_on_unless_turned_off():
    assert config.archive_unix({}) is True
    assert config.archive_unix({"stage": {"archive_unix_executables": False}}) is False
    with pytest.raises(SystemExit):
        config.archive_unix({"stage": {"archive_unix_executables": "no"}})


def test_configured_app_name_wins():
    assert config.app_name({"app_name": "SD Runner"}, ["SDRunner"]) == "SD Runner"
