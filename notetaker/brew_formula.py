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
