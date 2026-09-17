import hashlib
import json
from pathlib import Path
import tarfile

import pytest

from pt60 import Dataset, fetch


@pytest.fixture
def release(tmp_path):
    root = tmp_path / "release"
    (root / "validation").mkdir(parents=True)
    content = b"case_id,season,timestamp_utc\nCASE_A,SUMMER,2025-07-07T00:15:00+00:00\n"
    (root / "validation/seasonal_week_validation.csv").write_bytes(content)
    (root / "manifest.json").write_text(json.dumps({"dataset": "PT60", "version": "fixture", "files": [{"path": "validation/seasonal_week_validation.csv", "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}]}))
    return root


def test_detect_corruption_and_reject_paths(release):
    ds = Dataset(release)
    assert ds.verify()["status"] == "PASS"
    with pytest.raises(ValueError): ds.path("../outside")
    with pytest.raises(ValueError): ds.load_input("../outside")
    ds.path("validation/seasonal_week_validation.csv").write_text("corrupt")
    assert ds.verify()["status"] == "FAIL"


def test_protect_release_and_existing_results(release, tmp_path):
    ds = Dataset(release)
    with pytest.raises(ValueError): ds.init_example(release / "new")
    with pytest.raises(ValueError): ds._output(release.parent)
    out = tmp_path / "results"; out.mkdir(); (out / "valuable.txt").write_text("keep")
    with pytest.raises(ValueError): ds._run("unused.py", [], out)
    assert (out / "valuable.txt").read_text() == "keep"


def test_fetch_checks_hash_and_payload_before_publish(release, tmp_path):
    archive = tmp_path / "test.tar.gz"
    with tarfile.open(archive, "w:gz") as tar: tar.add(release, arcname="release")
    h = hashlib.sha256(archive.read_bytes()).hexdigest()
    out = tmp_path / "installed"
    with pytest.raises(ValueError): fetch(archive, out, "0" * 64)
    assert not out.exists()
    fetch(archive, out, h)
    assert Dataset(out).verify()["status"] == "PASS"
    with pytest.raises(ValueError): fetch(archive, out, h)


def test_reject_archive_link(release, tmp_path):
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("release/link"); info.type = tarfile.SYMTYPE; info.linkname = "/tmp"
        tar.addfile(info)
    with pytest.raises(ValueError): fetch(archive, tmp_path / "out", hashlib.sha256(archive.read_bytes()).hexdigest())


def test_case_filter_is_copy(release):
    ds = Dataset(release)
    assert len(ds.cases("summer")) == 1
    assert ds.cases("WINTER") == []
    ds.cases()[0]["case_id"] = "mutated"
    assert ds.case("CASE_A") is not None


def test_custom_result_rejects_different_template(release, tmp_path):
    from pt60.viewer import load_custom
    (release / "model").mkdir()
    (release / "model/model_template.json").write_text("{}")
    out = tmp_path / "custom"; out.mkdir()
    (out / "summary.json").write_text(json.dumps({"converged": True}))
    (out / "run_manifest.json").write_text(json.dumps({"model_input": {"sha256": "wrong"}}))
    with pytest.raises(ValueError, match="different topology"):
        load_custom(Dataset(release), out)


def test_web_export_does_not_publish_corrupt_release(release, tmp_path):
    from pt60.web_export import export_web
    ds = Dataset(release)
    ds.path("validation/seasonal_week_validation.csv").write_text("corrupt")
    target = tmp_path / "web"
    with pytest.raises(ValueError, match="integrity"):
        export_web(ds, target)
    assert not target.exists()
    target.mkdir()
    (target / "existing.json").write_text("keep")
    with pytest.raises(ValueError, match="new directory"):
        export_web(ds, target)
    assert (target / "existing.json").read_text() == "keep"
