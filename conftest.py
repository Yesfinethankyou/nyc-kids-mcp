# TEMPORARY debug hook (branch debug/ci-collection) — surfaces collection
# errors as GitHub Actions annotations because run logs aren't readable.
import importlib.metadata as md
import sys


def _ann(level, msg):
    msg = str(msg).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{msg[-3500:]}", flush=True)

def pytest_configure(config):
    vers = []
    pkgs = ["mcp", "starlette", "pydantic", "httpx", "uvicorn", "curl_cffi", "selectolax", "pytest"]
    for p in pkgs:
        try:
            vers.append(f"{p}={md.version(p)}")
        except Exception as e:
            vers.append(f"{p}=? {e}")
    _ann("warning", "py=" + sys.version.split()[0] + " " + " ".join(vers))

def pytest_collectreport(report):
    if report.failed:
        _ann("error", f"COLLECT {report.nodeid}: {report.longreprtext}")

def pytest_internalerror(excrepr, excinfo):
    _ann("error", f"INTERNAL: {excrepr}")

def pytest_sessionfinish(session, exitstatus):
    _ann(
        "warning",
        f"exit={exitstatus} collected={session.testscollected} failed={session.testsfailed}",
    )
