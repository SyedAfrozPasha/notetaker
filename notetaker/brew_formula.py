import re
from pathlib import Path

GITHUB_URL = "https://github.com/SyedAfrozPasha/notetaker"

# One service only: `notetaker dashboard` runs the web dashboard *and* the
# menu bar app in a single process. A separate `notetaker-menubar` service
# (which older installs had) would put a second icon in the menu bar.
SERVICE_DESCRIPTIONS = {
    "dashboard": "Notetaker web dashboard + menu bar app (brew services wrapper — builds nothing itself)",
}


def generate_formula(service: str, repo_dir: Path, version: str) -> str:
    """Returns the Ruby source for a personal Homebrew formula that wraps
    an existing `notetaker` installation (already set up by ./install.sh)
    as a `brew services`-managed background process — this formula never
    builds or installs any code of its own. `service` must be a key of
    SERVICE_DESCRIPTIONS, matching the `notetaker <service>` CLI subcommand
    it runs.
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
    # process, pointing at that already-existing installation. Named
    # WRAPPER_INFO (not README) because Homebrew treats a handful of
    # standard doc filenames as "metadata only" and refuses to consider
    # a formula that installs nothing but one of those as having
    # installed anything at all.
    (prefix/"WRAPPER_INFO").write <<~EOS
      This formula does not install any files of its own.
      See {repo_dir}/install.sh for the real notetaker installation.
    EOS
  end

  service do
    run ["{notetaker_bin}", "{service}"]
    keep_alive true
    environment_variables PATH: std_service_path_env
    log_path var/"log/notetaker-{service}.log"
    error_log_path var/"log/notetaker-{service}.log"
  end
end
'''


def _read_version(repo_dir: Path) -> str:
    pyproject_text = (repo_dir / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', pyproject_text, re.MULTILINE)
    if not match:
        raise ValueError(f"could not find a version in {repo_dir / 'pyproject.toml'}")
    return match.group(1)


def write_formulas(repo_dir: Path, version: str | None = None) -> list[Path]:
    """Writes one notetaker-<service>.rb per SERVICE_DESCRIPTIONS entry into
    repo_dir/Formula/ (creating that directory if needed). If `version` is
    not given, reads it from repo_dir/pyproject.toml. Returns the list of
    paths written.
    """
    if version is None:
        version = _read_version(repo_dir)
    formula_dir = repo_dir / "Formula"
    formula_dir.mkdir(exist_ok=True)
    written = []
    for service in SERVICE_DESCRIPTIONS:
        path = formula_dir / f"notetaker-{service}.rb"
        path.write_text(generate_formula(service, repo_dir, version))
        written.append(path)
    return written


if __name__ == "__main__":
    _repo_dir = Path(__file__).resolve().parent.parent
    for _path in write_formulas(_repo_dir):
        print(f"wrote {_path}")
