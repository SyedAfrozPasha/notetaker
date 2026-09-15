# Brew Services Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement ADR 0003 — let `brew services` manage the menu bar app and dashboard as auto-starting, crash-restarting background processes, via two personal (non-distributed) Homebrew formulas that wrap the already-existing `install.sh`-created `.venv` rather than building anything of their own.

**Architecture:** A new `notetaker/brew_formula.py` module generates two Ruby formula files (`Formula/notetaker-dashboard.rb`, `Formula/notetaker-menubar.rb`) into the repo, each a thin wrapper whose `service do` block points directly at `<this-repo>/.venv/bin/notetaker <dashboard|menubar>` — the exact executable `install.sh` already sets up. Neither formula fetches, builds, or installs any code of its own; each formula's `install` method is a no-op placeholder, and its `url` (required by Homebrew) points back at this same repo via a local `:git` checkout purely to satisfy Homebrew's bookkeeping. `install.sh` regenerates both files on every run (they bake in this machine's absolute path, so they can't be committed to git or shared across machines). This is deliberately NOT a real, distributable package — see ADR 0003 for why a full formula/tap is out of scope until this tool has users besides its author.

**Tech Stack:** No new Python dependencies. Ruby (Homebrew's formula language) is generated as plain text — no Ruby tooling is added to this project.

**Spec:** `docs/adr/0003-brew-services-for-ui-processes-no-app-packaging.md` (the decision this plan implements) and the grilling session it came from (`docs/superpowers/specs/2026-09-12-notetaker-ui-design.md`'s Architecture section: *"the menu bar app and dashboard run via `brew services` (a local/personal Homebrew formula) rather than manual subcommands or hand-rolled LaunchAgents"*).

## Global Constraints

- The generated formulas must never be committed to git — each one hard-codes this machine's absolute repo path in its `url` and `service do run [...]` lines, so a different machine (or a re-clone to a different path) needs its own freshly-generated copy. `install.sh` regenerates them on every run specifically so this is never a stale, manual step.
- Neither formula's `install` method may fetch, compile, or `pip install` anything — the actual Python environment is `install.sh`'s job, already done by the time these formulas are generated. A formula's only job is the `service do` block.
- Real `brew services start <formula>` registers a **persistent** LaunchAgent (auto-starts at every future login until explicitly stopped) — no task in this plan may invoke it for real, and no automated test may invoke it for real either. Verifying that a generated formula's *service definition* (the command it would run) is correct is fine and required (Task 4); actually starting the service is a manual step for a human, done deliberately, at the very end of this plan (see Final check).
- This plan's one real-system integration test (Task 4) still does real, reversible `brew tap`/`brew install`/`brew uninstall`/`brew untap` calls — mark it `@pytest.mark.integration` (matching this project's existing convention for the faster-whisper download test) so it's excluded from the default fast suite (`pytest -m "not integration"`) and only runs when explicitly requested. It must clean up (uninstall + untap) even if an assertion fails.
- Run `.venv/bin/pytest -q -m "not integration"` after every task; it must stay green (currently 267 passed total / 266 + 1 deselected on the fast filter) with only the new tests added by that task on top, except Task 4, which adds one new *integration*-marked test that does not run in that filtered count.

---

## Task 1: `generate_formula` — pure function producing one formula's Ruby source

**Files:**
- Create: `notetaker/brew_formula.py`
- Test: `tests/test_brew_formula.py`

**Interfaces:**
- Produces: `notetaker.brew_formula.generate_formula(service: str, repo_dir: Path, version: str) -> str` — `service` is `"dashboard"` or `"menubar"` (raises `KeyError` for anything else); returns the complete Ruby source for a formula named `Notetaker{Dashboard,Menubar}` whose `service do` block runs `<repo_dir>/.venv/bin/notetaker <service>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_brew_formula.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_brew_formula.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'notetaker.brew_formula'`.

- [ ] **Step 3: Implement `generate_formula`**

Create `notetaker/brew_formula.py`:

```python
from pathlib import Path

GITHUB_URL = "https://github.com/SyedAfrozPasha/notetaker"

SERVICE_DESCRIPTIONS = {
    "dashboard": "Notetaker local web dashboard (brew services wrapper — builds nothing itself)",
    "menubar": "Notetaker menu bar app (brew services wrapper — builds nothing itself)",
}


def generate_formula(service: str, repo_dir: Path, version: str) -> str:
    """Returns the Ruby source for a personal Homebrew formula that wraps
    an existing `notetaker` installation (already set up by ./install.sh)
    as a `brew services`-managed background process — this formula never
    builds or installs any code of its own. `service` is "dashboard" or
    "menubar", matching the `notetaker <service>` CLI subcommand it runs.
    """
    class_name = f"Notetaker{service.capitalize()}"
    desc = SERVICE_DESCRIPTIONS[service]
    notetaker_bin = repo_dir / ".venv" / "bin" / "notetaker"
    return f'''class {class_name} < Formula
  desc "{desc}"
  homepage "{GITHUB_URL}"
  url "{repo_dir}", using: :git, branch: "main"
  version "{version}"

  def install
    # This formula intentionally builds nothing — notetaker itself is
    # installed separately by ./install.sh into its own .venv. This
    # formula exists solely so `brew services` can manage the {service}
    # process, pointing at that already-existing installation.
    (prefix/"README").write <<~EOS
      This formula does not install any files of its own.
      See {repo_dir}/install.sh for the real notetaker installation.
    EOS
  end

  service do
    run ["{notetaker_bin}", "{service}"]
    keep_alive true
    log_path var/"log/notetaker-{service}.log"
    error_log_path var/"log/notetaker-{service}.log"
  end
end
'''
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_brew_formula.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 5: Run the full fast suite**

Run: `.venv/bin/pytest -q -m "not integration"`
Expected: PASS, 269 passed, 1 deselected (266 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add notetaker/brew_formula.py tests/test_brew_formula.py
git commit -m "feat: add generate_formula for personal brew services formulas"
```

---

## Task 2: `write_formulas` — writes both formula files, reading the version from `pyproject.toml`

**Files:**
- Modify: `notetaker/brew_formula.py`
- Test: `tests/test_brew_formula.py`

**Interfaces:**
- Consumes: `generate_formula(service, repo_dir, version) -> str` (Task 1).
- Produces: `notetaker.brew_formula.write_formulas(repo_dir: Path, version: str | None = None) -> list[Path]` — writes `notetaker-dashboard.rb` and `notetaker-menubar.rb` into `repo_dir/Formula/` (creating that directory if missing), reading `version` from `repo_dir/pyproject.toml` if not given explicitly, and returns the two paths written. A `if __name__ == "__main__":` block lets this module be run directly (`python -m notetaker.brew_formula`) against notetaker's own real repo location.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_brew_formula.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_brew_formula.py -k write_formulas -v`
Expected: FAIL — `ImportError: cannot import name 'write_formulas'`.

- [ ] **Step 3: Implement `write_formulas`**

Add to `notetaker/brew_formula.py`. First change the top import line from `from pathlib import Path` to:

```python
import re
from pathlib import Path
```

Then add, after `generate_formula`:

```python
def _read_version(repo_dir: Path) -> str:
    pyproject_text = (repo_dir / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', pyproject_text, re.MULTILINE)
    if not match:
        raise ValueError(f"could not find a version in {repo_dir / 'pyproject.toml'}")
    return match.group(1)


def write_formulas(repo_dir: Path, version: str | None = None) -> list[Path]:
    """Writes notetaker-dashboard.rb and notetaker-menubar.rb into
    repo_dir/Formula/ (creating that directory if needed). If `version` is
    not given, reads it from repo_dir/pyproject.toml. Returns the list of
    paths written.
    """
    if version is None:
        version = _read_version(repo_dir)
    formula_dir = repo_dir / "Formula"
    formula_dir.mkdir(exist_ok=True)
    written = []
    for service in ("dashboard", "menubar"):
        path = formula_dir / f"notetaker-{service}.rb"
        path.write_text(generate_formula(service, repo_dir, version))
        written.append(path)
    return written


if __name__ == "__main__":
    _repo_dir = Path(__file__).resolve().parent.parent
    for _path in write_formulas(_repo_dir):
        print(f"wrote {_path}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_brew_formula.py -v`
Expected: PASS, 6 passed (3 from Task 1 + 3 new).

- [ ] **Step 5: Run the full fast suite**

Run: `.venv/bin/pytest -q -m "not integration"`
Expected: PASS, 272 passed, 1 deselected (269 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add notetaker/brew_formula.py tests/test_brew_formula.py
git commit -m "feat: add write_formulas and a python -m entry point"
```

---

## Task 3: Wire into `install.sh`, `.gitignore` the generated files, document the workflow

**Files:**
- Modify: `install.sh`
- Modify: `.gitignore`
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `python -m notetaker.brew_formula` (Task 2's entry point).
- Produces: no new Python interface — this task is wiring and documentation only.

- [ ] **Step 1: Add formula generation to `install.sh`**

In `install.sh`, find the existing tail:

```bash
echo ""
echo "Installed. Make sure $BIN_DIR is on your PATH, then run:"
echo "  notetaker init"
```

Change it to:

```bash
echo ""
echo "Generating personal Homebrew formulas for 'brew services' (dashboard + menu bar)..."
"$VENV_DIR/bin/python" -m notetaker.brew_formula

echo ""
echo "Installed. Make sure $BIN_DIR is on your PATH, then run:"
echo "  notetaker init"
```

- [ ] **Step 2: Gitignore the generated formulas**

In `.gitignore`, add a new section at the end:

```
# Generated by notetaker/brew_formula.py (via install.sh) — bakes in this
# machine's absolute repo path, so it can never be shared across machines.
/Formula/*.rb
```

- [ ] **Step 3: Document the workflow in README.md**

In `README.md`, after the existing "Usage" section's closing code block (the block ending with `notetaker show-api-key`) and before "## A note on recording consent", add a new section:

```markdown
## Menu bar app & dashboard (via `brew services`)

`./install.sh` also generates two personal Homebrew formulas (`Formula/notetaker-dashboard.rb`,
`Formula/notetaker-menubar.rb`) — not published anywhere, just local wrappers so `brew services`
can auto-start and crash-restart these two long-running processes, the same way you'd manage any
other background service on a Mac where Homebrew is the sanctioned install path. Neither formula
builds anything; they just point `brew services` at the `.venv/bin/notetaker` that `./install.sh`
already set up. See `docs/adr/0003-brew-services-for-ui-processes-no-app-packaging.md` for why.

```bash
# One-time: create a local (unpublished) tap — brew tap-new avoids the
# "clone from a path" mechanism, which would silently drop the gitignored
# formula files below.
brew tap-new syedafrozpasha/notetaker --no-git

# Every time ./install.sh regenerates the formulas (first run, or after
# moving/re-cloning this repo), copy them into that tap:
cp Formula/*.rb "$(brew --repository syedafrozpasha/notetaker)/Formula/"

brew install syedafrozpasha/notetaker/notetaker-dashboard
brew install syedafrozpasha/notetaker/notetaker-menubar

brew services start notetaker-dashboard   # http://127.0.0.1:8420
brew services start notetaker-menubar

brew services list                        # check status
brew services stop notetaker-dashboard    # or notetaker-menubar
```

Re-run `./install.sh` any time you move or re-clone this repo — the formulas bake in an absolute
path and must be regenerated, then re-copied into the tap (the `cp` step above) and
`brew uninstall`/`brew install` again, if that path changes.
```

(NOTE — corrected post-implementation: the original text of this section documented `brew tap
syedafrozpasha/notetaker "$(pwd)"`, which the final whole-branch review found can never work —
`brew tap <name> <local-path>` does a real `git clone`, which only carries committed files, and
`Formula/*.rb` is gitignored by this same plan's Global Constraints. The `tap-new` + `cp` recipe
above is the corrected, verified-working replacement; see the branch's final fix commit. This
note is outside the fence deliberately — everything inside it is meant to be pasted into
README.md verbatim.)

- [ ] **Step 4: Update CLAUDE.md's architecture summary**

In `CLAUDE.md`, find the `config.py` bullet (the last one in the module list):

```
- `config.py` — loads/validates `~/.notetaker/config.yaml`.
```

Change it to:

```
- `config.py` — loads/validates `~/.notetaker/config.yaml`.
- `brew_formula.py` — generates two personal (non-distributed) Homebrew formula files into `Formula/` (gitignored — they bake in this machine's absolute repo path), one per `brew services`-managed background process (`notetaker-dashboard`, `notetaker-menubar`). Each formula builds nothing itself; its `service do` block just points `brew services` at the `.venv/bin/notetaker <subcommand>` that `install.sh` already set up. Regenerated by `install.sh` on every run — see ADR 0003 for why `brew services` instead of hand-rolled LaunchAgents or a full formula/tap.
```

- [ ] **Step 5: Run install.sh end to end and confirm the formulas are generated correctly**

Run: `./install.sh`
Expected: completes successfully; prints `wrote <repo>/Formula/notetaker-dashboard.rb` and `wrote <repo>/Formula/notetaker-menubar.rb` near the end, before the final "Installed." message.

Run: `cat Formula/notetaker-dashboard.rb`
Expected: a real Ruby formula file whose `url` and `run [...]` lines contain this repo's actual absolute path (not a placeholder), and whose `version` matches `pyproject.toml`'s current version.

- [ ] **Step 6: Run the full fast suite**

Run: `.venv/bin/pytest -q -m "not integration"`
Expected: PASS, 272 passed, 1 deselected (unchanged — this task adds no new tests, only wiring and docs).

- [ ] **Step 7: Commit**

```bash
git add install.sh .gitignore README.md CLAUDE.md
git commit -m "feat: wire brew formula generation into install.sh and document the workflow"
```

(Do not `git add Formula/` — the generated `.rb` files are gitignored per Step 2 and must not be committed.)

---

## Task 4: Real `brew` integration test — confirms a generated formula is genuinely accepted

This is the one task in this plan that talks to the real `brew` binary. It proves the *shape* of a generated formula is one Homebrew actually accepts (a `url`-only-for-bookkeeping, no-op-`install`, `service`-only formula) — something Tasks 1-2's pure-Python tests can assert the right *text* was generated, but can't prove Homebrew itself is happy with that text. It never calls `brew services start` — see this task's own Global-Constraints-derived rule above.

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_brew_formula.py`

**Interfaces:**
- Consumes: `write_formulas(repo_dir, version)` (Task 2).
- Produces: no new production code — one new integration-marked test.

- [ ] **Step 1: Broaden the `integration` marker's description**

This project already has an `integration` pytest marker (used by the faster-whisper download test), registered in `pyproject.toml` as:

```toml
markers = ["integration: slower tests that download/run a real faster-whisper model"]
```

This task reuses that same marker for a differently-shaped slow test (real `brew` calls, not a model download), so its description is now too narrow. Change that line to:

```toml
markers = ["integration: slower tests that touch a real external system (a downloaded model, brew, etc.)"]
```

- [ ] **Step 2: Write the failing test**

Add `import os`, `import shutil`, `import subprocess`, and `import uuid` to `tests/test_brew_formula.py`'s existing top-of-file import block (alongside the `import pytest` already there from Task 1) — not inline further down the file. Then add the test function itself anywhere below the existing tests:

```python
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

    tap_name = f"notetaker-test-{uuid.uuid4().hex[:8]}"
    full_tap = f"local/{tap_name}"
    formula_ref = f"{full_tap}/notetaker-dashboard"

    try:
        subprocess.run(["brew", "tap", full_tap, str(fake_repo)], check=True)
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
```

- [ ] **Step 3: Run the test to verify it fails for the expected reason first, then passes**

Run: `.venv/bin/pytest tests/test_brew_formula.py -k real_brew -v -m integration`
Expected: this test has no implementation to fail against (there's nothing to implement in this task — `write_formulas` already exists from Task 2) — so instead of a RED step, just run it now and confirm it PASSES. If `brew` isn't installed on the machine running this, it SKIPs cleanly instead of failing.
Expected: PASS (or SKIPPED if `brew` is unavailable), 1 passed (or 1 skipped), typically 15-40 seconds (real `brew tap`/`install`/`uninstall`/`untap` calls are slow — this is expected and why it's integration-marked).

- [ ] **Step 4: Confirm cleanup happened**

Run: `brew tap | grep notetaker-test`
Expected: no output — the temporary tap was fully removed by the test's `finally` block regardless of pass/fail.

- [ ] **Step 5: Run the full fast suite one more time to confirm this new test is excluded by default**

Run: `.venv/bin/pytest -q -m "not integration"`
Expected: PASS, 272 passed, 2 deselected — the pre-existing faster-whisper integration test plus this task's new real-brew integration test are both excluded by the `-m "not integration"` filter. Passed count is unchanged at 272; deselected count goes from 1 to 2.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/test_brew_formula.py
git commit -m "test: add real-brew integration test for generated formulas"
```

---

## Final check (do this after Task 4, before considering the plan done)

Run the full suite one more time, with and without the integration filter, and confirm both counts:

```bash
.venv/bin/pytest -q -m "not integration"
.venv/bin/pytest -q -m integration
```

Expected: `272 passed, 2 deselected` on the first (the faster-whisper test and this plan's new real-brew test are both excluded), and `2 passed` (or `1 passed, 1 skipped` if `faster-whisper`'s model isn't cached) on the second.

Then confirm no stray Homebrew state survived any test run:

```bash
brew tap | grep -i notetaker-test || echo "clean — no test taps remain"
brew list --formula | grep -i notetaker || echo "clean — no notetaker formulas installed"
```

**Manual step — do this yourself, deliberately, not as part of an automated run.** Everything above proves the generated formulas are correct and that real `brew` accepts them; it deliberately never starts a real service, since `brew services start` registers a LaunchAgent that will auto-start at every future login on this machine until you explicitly stop it. When you're ready to actually turn this on:

```bash
brew tap-new syedafrozpasha/notetaker --no-git
cp Formula/*.rb "$(brew --repository syedafrozpasha/notetaker)/Formula/"
brew install syedafrozpasha/notetaker/notetaker-dashboard
brew services start notetaker-dashboard
open http://127.0.0.1:8420
```

Confirm the dashboard loads, then decide whether to also `brew install .../notetaker-menubar` and `brew services start notetaker-menubar`. `brew services stop <name>` reverses it at any time; `brew services list` shows current status.
