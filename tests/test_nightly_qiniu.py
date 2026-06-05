import pytest

from scripts import nightly_run as nr


def test_validate_qiniu_config_rejects_bucket_path():
    config = {
        "qiniu_access_key": "ak",
        "qiniu_secret_key": "sk",
        "qiniu_bucket": "/auroai/",
        "qiniu_domain": "https://example.com",
    }

    with pytest.raises(RuntimeError, match="qiniu_bucket"):
        nr.validate_qiniu_config(config)


def test_copy_qiniu_publish_bundle_failed_only_excludes_xml_and_passed_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "REPORT_ROOT", tmp_path / "reports")
    bundle_dir = tmp_path / "recording_report_bundle_20260603_010203"
    failed_dir = bundle_dir / "artifacts" / "Failed_Case"
    passed_dir = bundle_dir / "artifacts" / "Passed_Case"
    failed_dir.mkdir(parents=True)
    passed_dir.mkdir(parents=True)

    (bundle_dir / "recording_report.html").write_text(
        """
        <div class="test-case show" data-status="FAILED">
          <div class="case-title">Failed Case</div>
        </div>
        <div class="test-case" data-status="PASSED">
          <div class="case-title">Passed Case</div>
        </div>
        """,
        encoding="utf-8",
    )
    (bundle_dir / "archive_manifest.json").write_text("{}", encoding="utf-8")
    (failed_dir / "001_step.png").write_bytes(b"png")
    (failed_dir / "001_step.xml").write_text("<xml/>", encoding="utf-8")
    (passed_dir / "001_step.png").write_bytes(b"png")

    failed_cases = nr.failed_cases_from_report(bundle_dir, limit=None)
    publish_dir = nr.copy_qiniu_publish_bundle(
        bundle_dir,
        {"qiniu_upload_scope": "failed_only", "qiniu_upload_xml": False},
        failed_cases,
    )

    assert (publish_dir / "recording_report.html").exists()
    assert (publish_dir / "archive_manifest.json").exists()
    assert (publish_dir / "artifacts" / "Failed_Case" / "001_step.png").exists()
    assert not (publish_dir / "artifacts" / "Failed_Case" / "001_step.xml").exists()
    assert not (publish_dir / "artifacts" / "Passed_Case" / "001_step.png").exists()


def test_qiniu_upload_file_retries_transient_urlopen_error(tmp_path, monkeypatch):
    calls = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        if len(calls) == 1:
            raise nr.urllib.error.URLError("ssl handshake timeout")
        return FakeResponse()

    monkeypatch.setattr(nr.urllib.request, "urlopen", fake_urlopen)
    upload_file = tmp_path / "recording_report.html"
    upload_file.write_text("<html></html>", encoding="utf-8")

    result = nr.qiniu_upload_file(
        {
            "qiniu_access_key": "ak",
            "qiniu_secret_key": "sk",
            "qiniu_bucket": "bucket",
            "qiniu_upload_url": "https://upload.example.com",
            "qiniu_upload_timeout_seconds": 5,
            "qiniu_upload_retries": 1,
            "qiniu_upload_retry_backoff_seconds": 0,
        },
        upload_file,
        "reports/recording_report.html",
    )

    assert result == {}
    assert len(calls) == 2
    assert calls[0] == ("https://upload.example.com", 5)


def test_run_nightly_falls_back_to_local_report_when_qiniu_upload_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "REPORT_ROOT", tmp_path / "reports")
    bundle_dir = nr.REPORT_ROOT / "recording_report_bundle_20260605_001739"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "archive_manifest.json").write_text(
        """
        {
          "total": 1,
          "passed": 0,
          "failed": 1,
          "errors": 0,
          "skipped": 0,
          "exitstatus": 1,
          "generated_at": "2026-06-05 00:17:39",
          "report_id": "recording_report_bundle_20260605_001739"
        }
        """,
        encoding="utf-8",
    )
    (bundle_dir / "recording_report.html").write_text(
        """
        <div class="test-case show" data-status="FAILED">
          <div class="case-title">Failed Case</div>
        </div>
        """,
        encoding="utf-8",
    )

    class FakeResult:
        returncode = 1

    payloads = []
    config = {
        "qiniu_enabled": True,
        "qiniu_fallback_to_local_report": True,
        "dingtalk_keyword": "test",
        "dingtalk_report_title": "Nightly",
    }

    monkeypatch.setattr(nr, "load_config", lambda: config)
    monkeypatch.setattr(nr, "clean_runtime_before_run", lambda config: None)
    monkeypatch.setattr(nr, "ensure_emulator", lambda config: None)
    monkeypatch.setattr(nr, "ensure_appium", lambda config: None)
    monkeypatch.setattr(nr, "register_runtime_cleanup", lambda config: None)
    monkeypatch.setattr(nr, "run_pytest", lambda config: FakeResult())
    monkeypatch.setattr(nr, "write_run_log", lambda result: None)
    monkeypatch.setattr(nr, "latest_report_bundle", lambda: bundle_dir)
    monkeypatch.setattr(
        nr,
        "upload_report_to_qiniu",
        lambda bundle, config, failed_cases: (_ for _ in ()).throw(
            TimeoutError("ssl handshake timeout")
        ),
    )
    monkeypatch.setattr(nr, "ensure_report_server", lambda config: "http://127.0.0.1:8876")
    monkeypatch.setattr(nr, "send_dingtalk", lambda config, payload: payloads.append(payload))

    args = type("Args", (), {"notify": "always"})()

    assert nr.run_nightly(args) == 1
    assert len(payloads) == 1
    text = payloads[0]["markdown"]["text"]
    assert "http://127.0.0.1:8876/recording_report_bundle_20260605_001739" in text
    assert "七牛上传失败，已降级为本机报告链接" in text
