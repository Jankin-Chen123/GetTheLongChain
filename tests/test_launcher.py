"""Offline launcher tests: disposable venvs, fake installers, no real server."""

import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest

from scripts import check_dependencies


ROOT = Path(__file__).resolve().parents[1]
PINS = dict(line.split("==") for line in (ROOT / "requirements.txt").read_text().splitlines() if line)


@pytest.mark.parametrize("name", PINS)
@pytest.mark.parametrize("state", ["correct", "older", "newer", "missing_metadata", "missing_module"])
def test_dependency_check(tmp_path, monkeypatch, capsys, name, state):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("# exact pins\n\n" + "\n".join(f"{n}=={v} # runtime" for n, v in PINS.items()))

    def version(distribution):
        if distribution == name:
            if state == "missing_metadata":
                raise importlib.metadata.PackageNotFoundError(distribution)
            if state in {"older", "newer"}:
                return "0.0.1" if state == "older" else "999.0.0"
        return PINS[distribution]

    monkeypatch.setattr(check_dependencies.importlib.metadata, "version", version)
    monkeypatch.setattr(check_dependencies.importlib.util, "find_spec", lambda module:
        None if module == name and state == "missing_module" else object())
    assert check_dependencies.check(requirements) == (0 if state == "correct" else 1)
    if state != "correct":
        assert f"{name}=={PINS[name]}" in capsys.readouterr().out


@pytest.mark.parametrize("content", ["", "# only comments", "fastapi>=0.1", "-r other.txt", "fastapi==0.1.*"])
def test_invalid_requirements_fail_closed(tmp_path, content):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(content)
    assert check_dependencies.check(requirements) == 2


def test_unreadable_requirements(tmp_path):
    assert check_dependencies.check(tmp_path / "missing.txt") == 2


def powershell_hosts():
    if sys.platform != "win32":
        return []
    candidates = [Path(os.environ.get("SystemRoot", r"C:\Windows")) /
                  "System32/WindowsPowerShell/v1.0/powershell.exe"]
    if found := shutil.which("pwsh"):
        candidates.append(Path(found))
    return [str(path) for path in candidates if path.is_file()]


@pytest.fixture(params=powershell_hosts() or [None], ids=lambda host: Path(host).stem if host else "no-windows-powershell")
def launcher(tmp_path, request, monkeypatch):
    if request.param is None:
        pytest.skip("Requires Windows and PowerShell; dependency checker tests remain portable")
    # Spaces exercise quoting of the project, checker, requirements and venv paths.
    project = tmp_path / "launcher project with spaces"
    project.mkdir()
    shutil.copy2(ROOT / "start.ps1", project)
    shutil.copy2(ROOT / "requirements.txt", project)
    shutil.copytree(ROOT / "scripts", project / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    stubs = project / "test_modules"
    stubs.mkdir()
    events = project / "events.jsonl"
    # PYTHONPATH is confined to child processes in this fixture. The fake pip
    # writes only these synthetic distributions, never a real site-packages.
    support = '''import json, os
from pathlib import Path
import sys

def record(action):
    with open(os.environ["LAUNCHER_EVENTS"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"action": action, "args": sys.argv[1:]}) + "\\n")

def install(requirements, force):
    directory = Path(__file__).parent
    for line in Path(requirements).read_text().splitlines():
        name, version = line.split("==")
        info = directory / (name + ".dist-info")
        metadata = info / "METADATA"
        if not force and metadata.exists() and ("Version: " + version + "\\n") in metadata.read_text():
            continue  # pip normally trusts matching metadata, even if files are missing.
        info.mkdir(exist_ok=True)
        (info / "METADATA").write_text("Metadata-Version: 2.1\\nName: " + name + "\\nVersion: " + version + "\\n")
        if name != "uvicorn":
            (directory / (name + ".py")).write_text("")
'''
    (stubs / "launcher_test_support.py").write_text(support)
    (stubs / "ensurepip.py").write_text('''from launcher_test_support import record
import os, sys
record("ensurepip")
sys.exit(12 if os.environ.get("LAUNCHER_FAILURE") == "ensurepip" else 0)
''')
    (stubs / "pip.py").write_text('''from launcher_test_support import record, install
import os, sys
record("pip")
if os.environ.get("LAUNCHER_FAILURE") == "pip":
    sys.exit(13)
if os.environ.get("LAUNCHER_FAILURE") != "no_repair":
    install(sys.argv[sys.argv.index("-r") + 1], "--force-reinstall" in sys.argv)
''')
    (stubs / "uvicorn.py").write_text('''from launcher_test_support import record
import os, sys
record("uvicorn")
sys.exit(int(os.environ.get("LAUNCHER_SERVER_EXIT", "0")))
''')
    monkeypatch.setenv("PYTHONPATH", str(stubs))
    monkeypatch.setenv("LAUNCHER_EVENTS", str(events))
    monkeypatch.delenv("LAUNCHER_FAILURE", raising=False)
    monkeypatch.delenv("LAUNCHER_SERVER_EXIT", raising=False)
    monkeypatch.delenv("PYTHONHOME", raising=False)
    # Make initial venv creation deterministic even if python is absent from PATH.
    wrapper = project / "invoke.ps1"
    python_path = sys.executable.replace("'", "''")
    wrapper.write_text(f"function python {{ & '{python_path}' @args }}\n& \"$PSScriptRoot/start.ps1\"\nexit $LASTEXITCODE\n")

    class Launcher:
        def prepare(self, versions=None, missing_module=None):
            venv.EnvBuilder(with_pip=False).create(project / ".venv")
            for name, version in (PINS if versions is None else versions).items():
                info = stubs / f"{name}.dist-info"
                info.mkdir(exist_ok=True)
                (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
                if name != "uvicorn" and name != missing_module:
                    (stubs / f"{name}.py").write_text("")

        def run(self):
            return subprocess.run([request.param, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
                                  cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)

        def actions(self):
            return [json.loads(line)["action"] for line in events.read_text().splitlines()] if events.exists() else []

    instance = Launcher()
    instance.project = project
    return instance


@pytest.mark.parametrize("state", ["first_install", "correct", "older", "newer", "missing_metadata", "missing_module"])
def test_launcher_installs_only_when_needed(launcher, state):
    if state != "first_install":
        versions = PINS.copy()
        if state in {"older", "newer"}:
            versions["fastapi"] = "0.0.1" if state == "older" else "999.0.0"
        if state == "missing_metadata":
            versions.pop("curl_cffi")
        launcher.prepare(versions, missing_module="curl_cffi" if state == "missing_module" else None)
    result = launcher.run()
    assert result.returncode == 0, result.stdout + result.stderr
    expected = ["uvicorn"] if state == "correct" else ["ensurepip", "pip", "uvicorn"]
    assert launcher.actions() == expected
    # A second launch must use the now-correct environment without reinstalling.
    result = launcher.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert launcher.actions() == expected + ["uvicorn"]
    records = [json.loads(line) for line in (launcher.project / "events.jsonl").read_text().splitlines()]
    assert records[-1]["args"] == ["server:app", "--host", "127.0.0.1", "--port", "8788", "--no-access-log"]
    if state != "correct":
        assert records[1]["args"] == ["install", "--force-reinstall", "-r", str(launcher.project / "requirements.txt")]


@pytest.mark.parametrize("failure, actions, message", [
    ("ensurepip", ["ensurepip"], "pip setup failed"),
    ("pip", ["ensurepip", "pip"], "Dependency installation failed"),
    ("no_repair", ["ensurepip", "pip"], "Dependency verification failed after installation"),
])
def test_launcher_stops_on_install_failure(launcher, monkeypatch, failure, actions, message):
    launcher.prepare({})
    monkeypatch.setenv("LAUNCHER_FAILURE", failure)
    result = launcher.run()
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert launcher.actions() == actions
    # Recovery on the next launch is allowed, and still needs installation.
    monkeypatch.delenv("LAUNCHER_FAILURE")
    result = launcher.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert launcher.actions() == actions + ["ensurepip", "pip", "uvicorn"]


def test_launcher_rereads_changed_requirements(launcher):
    launcher.prepare()
    assert launcher.run().returncode == 0
    requirements = launcher.project / "requirements.txt"
    requirements.write_text(requirements.read_text().replace(f"fastapi=={PINS['fastapi']}", "fastapi==0.116.0"))
    result = launcher.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert launcher.actions() == ["uvicorn", "ensurepip", "pip", "uvicorn"]


def test_launcher_stops_on_invalid_requirements(launcher):
    launcher.prepare()
    (launcher.project / "requirements.txt").write_text("fastapi>=0.1\n")
    result = launcher.run()
    assert result.returncode != 0
    assert "Dependency requirements check failed" in result.stdout + result.stderr
    assert launcher.actions() == []


def test_launcher_preserves_server_exit_code(launcher, monkeypatch):
    launcher.prepare()
    monkeypatch.setenv("LAUNCHER_SERVER_EXIT", "7")
    assert launcher.run().returncode == 7
    assert launcher.actions() == ["uvicorn"]


@pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows cmd.exe and Windows PowerShell")
def test_cmd_finds_system_powershell_without_path(tmp_path):
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.is_file():
        pytest.skip("Windows PowerShell is not installed at the system path")
    project = tmp_path / "cmd project with spaces"
    project.mkdir()
    shutil.copy2(ROOT / "start.cmd", project)
    # Exercise the real batch entry point without running the application.
    (project / "start.ps1").write_text("Set-Content -LiteralPath \"$PSScriptRoot/started.txt\" -Value 'ok'\nexit 0\n")
    env = os.environ.copy()
    env["PATH"] = ""
    result = subprocess.run([str(system_root / "System32/cmd.exe"), "/d", "/c", str(project / "start.cmd")],
                            cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / "started.txt").read_text().strip() == "ok"
