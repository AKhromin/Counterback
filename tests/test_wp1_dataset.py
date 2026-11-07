from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from test_historical_replay import recorded_day

import counterback_replay.dataset as dataset
from depth_recorder.storage import sha256_file

DAY = "2026-09-14"


def test_inventory_only_lists_files_and_keeps_missing_days_and_locked_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs, _ = recorded_day(tmp_path)
    monkeypatch.setattr(dataset, "_json", lambda _: pytest.fail("inventory read a payload"))
    result = dataset.inventory(inputs, "2026-09-15", end="2026-09-16", today=date(2026, 9, 17))
    assert [row["state"] for row in result["days"]] == [
        "discovered_unverified",
        "unavailable",
        "unavailable",
    ]
    assert [row["partition"] for row in result["days"]] == [
        "development",
        "evaluation",
        "evaluation",
    ]
    assert result["days"][0]["joint_missing_depth_hours"] == []
    assert result["days"][0]["sources"][0]["snapshots"] == 1
    assert result["days"][0]["observed_bytes"] > 0


def test_inventory_identity_and_immutable_outputs(tmp_path: Path) -> None:
    inputs, _ = recorded_day(tmp_path)
    result = dataset.inventory(inputs, "2026-10-15")
    path = tmp_path / "inventory.json"
    dataset.write_new(path, result)
    assert dataset.load_inventory(path) == result
    with pytest.raises(FileExistsError):
        dataset.write_new(path, result)
    result["evaluation_start"] = "2026-09-14"
    path.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="identity"):
        dataset.load_inventory(path)


def test_prepare_reuses_verified_bundle_without_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs, _ = recorded_day(tmp_path)
    cache = tmp_path / "cache"
    first = dataset.prepare_day(inputs, DAY, cache, checkpoint_every=3)
    assert first["state"] == "prepared" and not first["reused"]
    marker = dataset.validate_bundle(Path(first["bundle"]), first["dataset_id"])
    assert marker["summary"]["applied"] == 24
    assert marker["summary"]["duplicates"] == 24
    assert "WP2" in marker["qualification"]
    monkeypatch.setattr(
        dataset, "verify_day", lambda *a, **kw: pytest.fail("reverified cached day")
    )
    second = dataset.prepare_day(inputs, DAY, cache, checkpoint_every=3)
    assert second["reused"] and second["dataset_id"] == first["dataset_id"]
    assert len(list((cache / "bundles").iterdir())) == 1


def test_source_and_prepared_artifact_corruption_fail_closed(tmp_path: Path) -> None:
    inputs, _ = recorded_day(tmp_path)
    cache = tmp_path / "cache"
    first = dataset.prepare_day(inputs, DAY, cache)
    events = Path(first["bundle"]) / "replay/events.l2"
    events.write_bytes(events.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="cached artifact"):
        dataset.prepare_day(inputs, DAY, cache)
    raw = next((inputs[0].root / "btcusdt" / DAY).glob("depth-*.zst"))
    raw.write_bytes(raw.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        dataset.prepare_day(inputs, DAY, cache)


def test_rule_version_and_source_order_change_dataset_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs, _ = recorded_day(tmp_path)
    cache = tmp_path / "cache"
    original = dataset.prepare_day(inputs, DAY, cache)
    monkeypatch.setattr(dataset, "PREPARATION_VERSION", "test-version-2")
    changed = dataset.prepare_day(inputs, DAY, cache)
    assert changed["dataset_id"] != original["dataset_id"]
    left = dataset._fingerprint(inputs, "BTCUSDT", DAY)
    right = dataset._fingerprint(inputs[::-1], "BTCUSDT", DAY)
    assert dataset.digest(left) != dataset.digest(right)
    assert Path(original["bundle"]).is_dir()


def test_review_day_is_excluded_with_immutable_diagnostics(tmp_path: Path) -> None:
    inputs, _ = recorded_day(tmp_path)
    for source in inputs:
        directory = source.root / "btcusdt" / DAY
        path = directory / "depth-00.ndjson.zst"
        path.rename(path.with_suffix(".omitted"))
        mp = directory / f"manifest-{DAY}.json"
        value = json.loads(mp.read_text())
        value["files"] = [item for item in value["files"] if item["name"] != path.name]
        mp.write_text(json.dumps(value))
    result = dataset.prepare_day(inputs, DAY, tmp_path / "cache")
    assert result["state"] == "excluded" and result["report_status"] == "review"
    assert result["quality_issues"]
    assert (Path(result["diagnostics"]) / "exclusion.json").exists()
    assert not (tmp_path / "cache/bundles").exists()


def test_catalogue_records_failures_and_unselected_days(tmp_path: Path) -> None:
    inputs, _ = recorded_day(tmp_path)
    observed = dataset.inventory(inputs, "2026-09-15", end="2026-09-15", today=date(2026, 9, 16))
    result = dataset.prepare_catalogue(observed, [DAY], tmp_path / "cache")
    assert result["days"][0]["state"] == "prepared"
    assert result["days"][1]["state"] == "unavailable"
    assert result["days"][0]["partition"] == "development"
    assert result["days"][1]["partition"] == "evaluation"
    with pytest.raises(ValueError):
        dataset.prepare_catalogue(observed, ["2026-09-16"], tmp_path / "cache")
    # New unlisted depth input must not disappear behind a successful old report/cache.
    unlisted = inputs[0].root / "btcusdt" / DAY / "depth-25.ndjson.zst"
    unlisted.write_bytes(b"unlisted")
    failed = dataset.prepare_catalogue(observed, [DAY], tmp_path / "cache")
    assert failed["days"][0]["state"] == "failed_preparation"


def test_output_protection_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inputs, _ = recorded_day(tmp_path)
    arguments = [
        value for source in inputs for value in ("--input", f"{source.recorder_id}={source.root}")
    ]
    output = tmp_path / "inventory.json"
    dataset.main(
        ["inventory", *arguments, "--evaluation-start", "2026-10-15", "--output", str(output)]
    )
    assert json.loads(capsys.readouterr().out)["days"] == 1
    catalogue = tmp_path / "catalogue.json"
    dataset.main(
        [
            "prepare",
            "--inventory",
            str(output),
            "--date",
            DAY,
            "--cache",
            str(tmp_path / "cache"),
            "--output",
            str(catalogue),
        ]
    )
    assert json.loads(catalogue.read_text())["days"][0]["state"] == "prepared"
    with pytest.raises(ValueError, match="outside"):
        dataset.prepare_day(inputs, DAY, inputs[0].root / "cache")
    with pytest.raises(SystemExit):
        dataset.main(
            [
                "inventory",
                *arguments,
                "--evaluation-start",
                "2026-10-15",
                "--output",
                str(inputs[0].root / "forbidden.json"),
            ]
        )
    assert not (inputs[0].root / "forbidden.json").exists()


def test_missing_inputs_metadata_and_manifest_evidence(tmp_path: Path) -> None:
    inputs, _ = recorded_day(tmp_path)
    observed = dataset.inventory(inputs, "2026-10-15")
    directory = inputs[0].root / "btcusdt" / DAY
    manifest = directory / f"manifest-{DAY}.json"
    value = json.loads(manifest.read_text())
    value["files"][0].pop("sha256")
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="checksum"):
        dataset.prepare_day(inputs, DAY, tmp_path / "cache")
    manifest.rename(directory / "manifest.partial.json")
    result = dataset.prepare_catalogue(observed, [DAY], tmp_path / "cache")
    assert result["days"][0]["state"] == "unavailable"
    assert sha256_file(directory / "manifest.partial.json")


def test_no_current_day_or_invalid_scope_is_prepared(tmp_path: Path) -> None:
    inputs, _ = recorded_day(tmp_path)
    with pytest.raises(ValueError, match="completed"):
        dataset.prepare_day(inputs, "2099-01-01", tmp_path / "cache")
    with pytest.raises(ValueError, match="BTCUSDT"):
        dataset.inventory(inputs, "2026-10-15", symbol="..")
    with pytest.raises(ValueError, match="minimum"):
        dataset.inventory(inputs, "2026-10-15", minimum_recorders=3)
