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
