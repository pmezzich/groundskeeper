"""Tests for the factory-over-inline check (salesagent Pattern #8).

Raw ``session.add(<Model>(...))`` in a test file where ``<Model>Factory`` exists
must be flagged — and, unlike the repository-pattern structural guard, it must
fire even on files that guard would allowlist. Each test drives the real
production function with a constructed FileDiff; nothing is mocked.
"""

from __future__ import annotations

from collections.abc import Sequence

from groundskeeper.checks import check_raw_model_add, run_deterministic_checks
from groundskeeper.models import DiffLine, FileDiff, Severity, VerdictStatus


def _file(
    path: str,
    *added: str,
    removed: Sequence[str] = (),
    status: str = "modified",
    patch: str = "",
) -> FileDiff:
    return FileDiff(
        path=path,
        status=status,
        added_lines=[DiffLine(line_number=i + 1, content=c) for i, c in enumerate(added)],
        removed_lines=list(removed),
        patch=patch,
    )


class TestRawModelAdd:
    def test_known_factory_model_is_flagged(self):
        # MediaPackage has MediaPackageFactory — the recurring reviewer blocker.
        f_ = _file("tests/integration/test_buys.py", "    session.add(MediaPackage(")
        findings = check_raw_model_add([f_])
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "det/raw-model-add"
        assert f.status == VerdictStatus.UNCERTAIN
        assert f.severity == Severity.BLOCKING
        assert f.deterministic is True
        assert f.evidence == "tests/integration/test_buys.py:1"
        assert "MediaPackageFactory" in f.explanation

    def test_db_session_receiver_is_flagged(self):
        f_ = _file("tests/test_db.py", "    db_session.add(Tenant(tenant_id='t1'))")
        findings = check_raw_model_add([f_])
        assert len(findings) == 1
        assert findings[0].rule_id == "det/raw-model-add"

    def test_factory_discovered_from_same_pr_is_flagged(self):
        # Widget isn't in the curated set, but WidgetFactory is referenced
        # elsewhere in the PR — that proves the factory exists.
        files = [
            _file("tests/factories/widget.py", "class WidgetFactory(SQLAlchemyModelFactory):"),
            _file("tests/test_widget.py", "    session.add(Widget(name='w'))"),
        ]
        findings = check_raw_model_add(files)
        assert len(findings) == 1
        assert findings[0].evidence == "tests/test_widget.py:1"

    def test_no_factory_no_finding(self):
        # UnbackedThing has no factory in the set and none referenced in the PR.
        assert check_raw_model_add([_file("tests/test_x.py", "    session.add(UnbackedThing(id=1))")]) == []

    def test_non_test_file_ignored(self):
        assert check_raw_model_add([_file("src/repositories/buy.py", "    session.add(MediaPackage(")]) == []

    def test_non_python_file_ignored(self):
        assert check_raw_model_add([_file("tests/fixtures/data.md", "    session.add(MediaPackage(")]) == []

    def test_factory_usage_itself_not_flagged(self):
        # session.add(MediaPackageFactory(...)) is the CORRECT pattern.
        f_ = _file("tests/test_ok.py", "    session.add(MediaPackageFactory(tenant=t))")
        assert check_raw_model_add([f_]) == []

    def test_non_session_receiver_not_flagged(self):
        # a set/collection .add() is not a DB session — must not trip.
        assert check_raw_model_add([_file("tests/test_set.py", "    seen.add(Product(id=1))")]) == []

    def test_bare_model_reference_without_call_not_flagged(self):
        # session.add(model_instance) / session.add(Model) — no inline construction.
        files = [
            _file("tests/test_a.py", "    session.add(existing_tenant)"),
            _file("tests/test_b.py", "    session.add(Tenant)"),
        ]
        assert check_raw_model_add(files) == []

    def test_moved_line_not_flagged(self):
        # identical content also appears removed -> re-indent/move, not new code.
        f = FileDiff(
            path="tests/test_move.py",
            status="modified",
            added_lines=[DiffLine(line_number=1, content="    session.add(Tenant(tenant_id='t1'))")],
            removed_lines=["    session.add(Tenant(tenant_id='t1'))"],
        )
        assert check_raw_model_add([f]) == []


class TestAllowlistBypass:
    """The point of the check: it fires where the repository-pattern guard would
    exempt the file. It has no allowlist parameter and keys only on the pattern."""

    def test_fires_on_allowlisted_style_debt_file(self):
        # A pre-existing A2A integration test — exactly the kind of legacy file
        # the salesagent structural guard allowlists. The finding still fires.
        f = _file(
            "tests/integration/test_a2a_media_buys.py",
            "    session.add(MediaBuy(media_buy_id='mb1'))",
        )
        findings = check_raw_model_add([f])
        assert len(findings) == 1
        assert "allowlist" in findings[0].explanation

    def test_fires_even_when_surrounding_file_hand_rolls_construction(self):
        # Pre-existing raw adds in the removed/context lines don't grant amnesty
        # to a newly added one (only an identical moved line would).
        f = _file(
            "tests/integration/test_legacy.py",
            "    session.add(Product(product_id='p2'))",
            removed=["    session.add(Product(product_id='p1'))"],
        )
        findings = check_raw_model_add([f])
        assert len(findings) == 1
        assert findings[0].rule_id == "det/raw-model-add"


def test_registered_in_run_deterministic_checks():
    files = [_file("tests/integration/test_buys.py", "    session.add(MediaPackage(tenant_id='t1'))")]
    rule_ids = {f.rule_id for f in run_deterministic_checks(files)}
    assert "det/raw-model-add" in rule_ids
