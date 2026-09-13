import os
import shutil
import subprocess
import uuid

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


@pytest.mark.integration
def test_generated_formula_is_accepted_by_real_brew(tmp_path):
    """An end-to-end check that brew actually accepts a generated formula
    file: installs it into a throwaway local tap, confirms
    `brew services info` reports the exact command our service block
    specifies, then fully uninstalls and untaps. Never calls
    `brew services start` — that registers a real, persistent LaunchAgent,
    which no automated test should do on its own.
    """
    brew = shutil.which("brew")
    if brew is None:
        pytest.skip("brew is not installed on this machine")

    from notetaker.brew_formula import write_formulas

    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init", "-q"], cwd=fake_repo, check=True, env=git_env)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "x"], cwd=fake_repo, check=True, env=git_env
    )
    write_formulas(fake_repo, version="0.0.0-test")
    subprocess.run(["git", "add", "-A"], cwd=fake_repo, check=True, env=git_env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add formulas"], cwd=fake_repo, check=True, env=git_env
    )

    tap_name = f"notetaker-test-{uuid.uuid4().hex[:8]}"
    full_tap = f"local/{tap_name}"
    formula_ref = f"{full_tap}/notetaker-dashboard"

    try:
        tap_result = subprocess.run(
            ["brew", "tap", full_tap, str(fake_repo)], capture_output=True, text=True
        )
        if tap_result.returncode != 0:
            if "untrusted tap" in tap_result.stderr or "invalid syntax in tap" in tap_result.stderr:
                pytest.skip(
                    "this machine's brew requires an explicit `brew trust` for local taps "
                    "before it will load their formulas; run `brew trust <tap>` yourself to "
                    "verify manually, or use a brew build without the tap-trust gate"
                )
            tap_result.check_returncode()
        subprocess.run(["brew", "install", formula_ref], check=True)

        info = subprocess.run(
            ["brew", "services", "info", formula_ref, "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
        expected_command = str(fake_repo / ".venv" / "bin" / "notetaker") + " dashboard"
        assert expected_command in info.stdout
    finally:
        subprocess.run(["brew", "uninstall", formula_ref], check=False)
        subprocess.run(["brew", "untap", full_tap], check=False)
