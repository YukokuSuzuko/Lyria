"""Opt-in flushed test journal: retain exact failures before a native crash."""
import json
from pathlib import Path
_sink = None

def pytest_addoption(parser):
    parser.addoption("--lyria-journal", default=None)
    parser.addoption("--lyria-collected", default=None)


def pytest_collection_finish(session):
    target = session.config.getoption("--lyria-collected")
    if target:
        with Path(target).open("x", encoding="utf8") as stream:
            json.dump([item.nodeid for item in session.items], stream, ensure_ascii=False)

def pytest_configure(config):
    global _sink
    destination=config.getoption("--lyria-journal")
    if destination:
        path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
        _sink=path.open("x",encoding="utf8")

def pytest_runtest_logreport(report):
    if _sink is None:return
    record={"nodeid":report.nodeid,"when":report.when,"outcome":report.outcome,"duration":report.duration}
    if report.failed:record["failure"]=str(report.longrepr)
    _sink.write(json.dumps(record,ensure_ascii=True)+"\n");_sink.flush()

def pytest_unconfigure(config):
    global _sink
    if _sink is not None:_sink.close();_sink=None
