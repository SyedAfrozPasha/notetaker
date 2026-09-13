import pytest


def test_generate_formula_dashboard_has_correct_service_block(tmp_path):
    from notetaker.brew_formula import generate_formula

    formula = generate_formula("dashboard", tmp_path, "0.1.0")

    assert "class NotetakerDashboard < Formula" in formula
    assert f'url "{tmp_path}", using: :git, branch: "main"' in formula
    assert 'version "0.1.0"' in formula
    assert f'run ["{tmp_path}/.venv/bin/notetaker", "dashboard"]' in formula
    assert 'log_path var/"log/notetaker-dashboard.log"' in formula
    assert 'error_log_path var/"log/notetaker-dashboard.log"' in formula
    assert "keep_alive true" in formula


def test_generate_formula_menubar_has_correct_service_block(tmp_path):
    from notetaker.brew_formula import generate_formula

    formula = generate_formula("menubar", tmp_path, "0.1.0")

    assert "class NotetakerMenubar < Formula" in formula
    assert f'run ["{tmp_path}/.venv/bin/notetaker", "menubar"]' in formula
    assert 'log_path var/"log/notetaker-menubar.log"' in formula


def test_generate_formula_rejects_unknown_service(tmp_path):
    from notetaker.brew_formula import generate_formula

    with pytest.raises(KeyError):
        generate_formula("bogus", tmp_path, "0.1.0")


def test_write_formulas_creates_both_files_with_given_version(tmp_path):
    from notetaker.brew_formula import write_formulas

    written = write_formulas(tmp_path, version="9.9.9")

    assert len(written) == 2
    dashboard = (tmp_path / "Formula" / "notetaker-dashboard.rb").read_text()
    menubar = (tmp_path / "Formula" / "notetaker-menubar.rb").read_text()
    assert 'version "9.9.9"' in dashboard
    assert 'version "9.9.9"' in menubar


def test_write_formulas_reads_version_from_pyproject_when_not_given(tmp_path):
    from notetaker.brew_formula import write_formulas

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "notetaker"\nversion = "1.2.3"\n')

    write_formulas(tmp_path)

    dashboard = (tmp_path / "Formula" / "notetaker-dashboard.rb").read_text()
    assert 'version "1.2.3"' in dashboard


def test_write_formulas_creates_formula_directory_if_missing(tmp_path):
    from notetaker.brew_formula import write_formulas

    assert not (tmp_path / "Formula").exists()

    write_formulas(tmp_path, version="0.1.0")

    assert (tmp_path / "Formula").is_dir()
