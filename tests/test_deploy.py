"""
Deploy file tests.

These don't run the actual scheduler (which is Windows-only). They
validate that the files Task Scheduler will read are structurally
correct and that the install-task.ps1 substitution points are present.

If the XML schema drifts and these tests still pass but TS rejects
the file, the test should be expanded — but XML well-formedness +
right element paths is a strong enough sanity gate to catch the
common breaks (typos, missing namespaces, removed nodes).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DEPLOY = REPO_ROOT / "deploy" / "windows"

TS_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_task_xml_is_wellformed():
    """Task Scheduler will reject malformed XML before doing anything."""
    ET.parse(DEPLOY / "topstep-bot.xml")  # raises on parse error


def test_task_xml_has_substitution_targets():
    """install-task.ps1 expects these exact XPath targets to exist."""
    tree = ET.parse(DEPLOY / "topstep-bot.xml")
    root = tree.getroot()

    required = [
        ".//t:Principal/t:UserId",
        ".//t:Actions/t:Exec/t:Command",
        ".//t:Actions/t:Exec/t:WorkingDirectory",
        ".//t:CalendarTrigger/t:StartBoundary",
    ]
    for path in required:
        assert root.find(path, TS_NS) is not None, (
            f"Missing required substitution target: {path}"
        )


def test_task_xml_safety_settings():
    """
    The non-negotiable settings: only one instance, restart on failure,
    auto-stop via ExecutionTimeLimit. Two parallel bots managing the
    same account would be catastrophic.
    """
    tree = ET.parse(DEPLOY / "topstep-bot.xml")
    root = tree.getroot()

    multi = root.find(".//t:Settings/t:MultipleInstancesPolicy", TS_NS)
    assert multi is not None
    assert multi.text == "IgnoreNew", (
        f"MultipleInstancesPolicy must be IgnoreNew (got {multi.text!r}). "
        "Parallel bots would race on the same account."
    )

    timeout = root.find(".//t:Settings/t:ExecutionTimeLimit", TS_NS)
    assert timeout is not None
    # ISO 8601 duration. Must be set; "PT0S" means no limit, which we don't want.
    assert timeout.text != "PT0S"
    assert timeout.text.startswith("PT"), (
        f"ExecutionTimeLimit malformed: {timeout.text!r}"
    )

    restart = root.find(".//t:Settings/t:RestartOnFailure/t:Count", TS_NS)
    assert restart is not None
    assert int(restart.text) >= 1


def test_task_xml_runs_weekdays_only():
    """
    Futures markets close 16:00 ET Friday; running Sat/Sun would just
    burn power. If we ever change to 7-day operation, this test should
    be updated intentionally.
    """
    tree = ET.parse(DEPLOY / "topstep-bot.xml")
    root = tree.getroot()
    days = root.find(".//t:ScheduleByWeek/t:DaysOfWeek", TS_NS)
    assert days is not None

    day_tags = {child.tag.split("}")[-1] for child in days}
    assert day_tags == {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}


def test_start_bat_has_required_pieces():
    """
    The .bat is shell, not Python — no full parser. But we can grep
    for the structurally-required pieces. If any of these go missing,
    the launcher silently breaks at 6am tomorrow.
    """
    content = (DEPLOY / "start.bat").read_text()

    # Repo root resolution.
    assert "%~dp0" in content, "Lost the script-relative path resolution"

    # Venv activation.
    assert "activate.bat" in content, "Lost the venv activation"

    # Logs directory creation.
    assert "mkdir" in content and "logs" in content.lower()

    # The bot itself gets called as a module.
    assert "python" in content.lower()
    assert "app.main" in content, "Lost the entrypoint"

    # .env loading — secrets without this break live mode.
    assert ".env" in content, "Lost the .env sourcing"


def test_install_ps1_references_correct_xpath_targets():
    """
    The PowerShell installer hard-codes XPath strings to find
    substitution points. If the XML changes structure but the .ps1
    doesn't, the substitution silently does nothing. This test catches
    the divergence by checking the installer's strings against the
    actual XML.
    """
    ps_content = (DEPLOY / "install-task.ps1").read_text()
    tree = ET.parse(DEPLOY / "topstep-bot.xml")
    root = tree.getroot()

    # XPath patterns the installer claims to look for. Each must
    # actually resolve in the XML. This catches drift between
    # "what the installer thinks" and "what the XML has".
    referenced_xpaths = re.findall(
        r'SelectSingleNode\("([^"]+)"',
        ps_content,
    )
    assert referenced_xpaths, (
        "install-task.ps1 has no SelectSingleNode calls — was it gutted?"
    )

    for xpath in referenced_xpaths:
        # PowerShell's XPath uses //prefix style for "anywhere in tree".
        # ElementTree requires .//prefix. Translate.
        et_xpath = xpath
        if et_xpath.startswith("//"):
            et_xpath = "." + et_xpath
        resolved = root.find(et_xpath, TS_NS)
        assert resolved is not None, (
            f"install-task.ps1 references XPath {xpath!r} which doesn't "
            f"exist in topstep-bot.xml. Did the XML change without "
            f"updating the installer?"
        )


def test_env_example_documents_required_vars():
    """
    The .env.example must document every var that load_config() requires
    in live mode. If we add a new required var without updating the
    template, fresh installs break.
    """
    env_example = (REPO_ROOT / ".env.example").read_text()
    config_py = (REPO_ROOT / "app" / "config.py").read_text()

    # All TOPSTEP_BOT_ vars referenced in config.py must appear in .env.example.
    bot_vars = set(re.findall(r'TOPSTEP_BOT_[A-Z_]+', config_py))
    # These are the vars the user would set; consumed-but-not-set
    # internals would also match the regex but we don't have any.
    for v in bot_vars:
        assert v in env_example, (
            f"{v} is referenced in config.py but missing from .env.example"
        )


def test_readme_references_actual_filenames():
    """
    The README tells the operator which files to run. If those files
    get renamed without updating the README, the install instructions
    break. Cheap test, catches a real footgun.
    """
    readme = (DEPLOY / "README.md").read_text()
    for required_file in ("start.bat", "install-task.ps1", "topstep-bot.xml"):
        assert required_file in readme, (
            f"deploy/windows/README.md does not mention {required_file}"
        )
