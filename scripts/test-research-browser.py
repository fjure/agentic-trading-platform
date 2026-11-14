"""Run the mandatory Research browsers against a disposable, synthetic-only runtime."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.integration import research_browser_support as browser  # noqa: E402

NODE_VERSION = "v22.23.3"
PLAYWRIGHT = ROOT / "apps/dashboard/node_modules/@playwright/test/cli.js"
POSTGRES = (
    "postgres:18.6-trixie@sha256:"
    "74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
)


def run(*args, env=None, timeout=240):
    subprocess.run(args, env=env, cwd=ROOT, check=True, timeout=timeout)


def prepare(base_image, test_image):
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node is None or npm is None:
        raise RuntimeError("Node and npm are required")
    version = subprocess.check_output([node, "--version"], text=True, timeout=10).strip()
    if version != NODE_VERSION:
        raise RuntimeError(f"Node {NODE_VERSION} is required")
    run("docker", "version", "--format", "{{.Server.Version}}")
    run("docker", "pull", POSTGRES)
    run("docker", "build", "-t", base_image, "-f", "infra/containers/Dockerfile", ".")
    run(
        "docker", "build", "-t", test_image, "-f", "infra/containers/research-test.Dockerfile",
        "--build-arg", f"RESEARCH_TEST_BASE={base_image}", ".",
    )
    run(npm, "--prefix", "apps/dashboard", "ci", "--no-audit", "--no-fund")
    run(npm, "--prefix", "apps/dashboard", "run", "build")
    chromium = ["chromium"] if os.name == "nt" else ["--with-deps", "chromium"]
    run(node, str(PLAYWRIGHT), "install", *chromium)
    return node


def collect_report(root, path):
    """Retain only test identities and result types from private Playwright reports."""
    if not path.is_file():
        return
    for case in ElementTree.parse(path).iter("testcase"):
        result = ElementTree.SubElement(
            root,
            "testcase",
            classname=case.attrib["classname"].replace("\\", "/").rsplit("/", 1)[-1],
            name=case.attrib["name"],
        )
        for child in case:
            if child.tag in {"failure", "error", "skipped"}:
                ElementTree.SubElement(result, child.tag)


def execute(node, test_image, junitxml):
    with tempfile.TemporaryDirectory(prefix="atl-research-browser-") as temp:
        integrated, companion = (Path(temp) / name for name in ("integrated.xml", "companion.xml"))
        sql_passed = False
        success = True
        try:
            browser.host(
                agent=True, node=node, junitxml=integrated,
                postgres_image=POSTGRES, test_image=test_image,
            )
            sql_passed = True
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"Isolated Research Browser failed ({type(error).__name__}).", file=sys.stderr)
            success = False

        try:
            run(
                node, str(PLAYWRIGHT), "test", "--config", "playwright.config.ts",
                "agent-companion.spec.ts", "--reporter=junit",
                env=os.environ | {"PLAYWRIGHT_JUNIT_OUTPUT_FILE": str(companion)},
            )
        except (OSError, subprocess.SubprocessError) as error:
            print(f"Research Companion Browser failed ({type(error).__name__}).", file=sys.stderr)
            success = False

        suite = ElementTree.Element("testsuite", name="Research browser")
        for report in (integrated, companion):
            collect_report(suite, report)
        case = ElementTree.SubElement(
            suite, "testcase", classname="research_browser", name="verify_persisted_state",
        )
        if not sql_passed:
            ElementTree.SubElement(case, "failure", message="SQL assertions were not confirmed")
        suite.set("tests", str(len(suite)))
        suite.set("failures", str(len(suite.findall("testcase/failure"))))
        suite.set("errors", str(len(suite.findall("testcase/error"))))
        suite.set("skipped", str(len(suite.findall("testcase/skipped"))))
        junitxml.parent.mkdir(parents=True, exist_ok=True)
        ElementTree.ElementTree(suite).write(junitxml, encoding="utf-8", xml_declaration=True)
    return 0 if success else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junitxml", type=Path, required=True)
    args = parser.parse_args()
    identity = uuid4().hex
    base_image = f"atl-research-browser-base:{identity}"
    test_image = f"atl-research-browser:{identity}"
    try:
        try:
            node = prepare(base_image, test_image)
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            print(
                f"Research browser environment unavailable ({type(error).__name__}).",
                file=sys.stderr,
            )
            return 2
        return execute(node, test_image, args.junitxml)
    finally:
        if shutil.which("docker"):
            subprocess.run(
                ["docker", "image", "rm", test_image, base_image],
                cwd=ROOT, capture_output=True, check=False, timeout=60,
            )


if __name__ == "__main__":
    raise SystemExit(main())
