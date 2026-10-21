import importlib

import pytest
from sqlalchemy.exc import IntegrityError


def backend():
    return importlib.import_module("business.cases")


@pytest.mark.parametrize("code", [1062, 1452, 1048, 1213, "1062", None])
def test_duplicate_key_detection_only_accepts_mysql_1062(code):
    module = backend()
    assert hasattr(module, "is_mysql_duplicate_key"), "Duplicate-code helper is missing"
    original = Exception() if code is None else Exception(code, "details must not be logged")
    error = IntegrityError("INSERT", None, original)
    assert module.is_mysql_duplicate_key(error) is (code == 1062)


def payload(**changes):
    result = dict(order_id="DEMO-001", case_type="logistics_exception", description=" test ")
    result.update(changes)
    return result


def test_missing_record_does_not_fabricate_a_case_id():
    module = backend()
    assert hasattr(module, "case_id_for_matching_request"), "Matching-request helper is missing"
    assert module.case_id_for_matching_request(None, payload()) is None


def test_matching_record_returns_the_old_case_regardless_of_json_key_order():
    module = backend()
    assert hasattr(module, "case_id_for_matching_request"), "Matching-request helper is missing"
    existing = {"request_payload": payload(), "case": {"case_id": "OLD-CASE"}}
    reordered = dict(reversed(list(payload().items())))
    assert module.case_id_for_matching_request(existing, reordered) == "OLD-CASE"


@pytest.mark.parametrize(
    "changes", [{"order_id": "DEMO-002"}, {"case_type": "refund_request"}, {"description": "test"}]
)
def test_changed_payload_is_a_conflict_and_does_not_overwrite_the_record(changes):
    module = backend()
    assert hasattr(module, "case_id_for_matching_request"), "Matching-request helper is missing"
    existing = {"request_payload": payload(), "case": {"case_id": "OLD-CASE"}}
    with pytest.raises(module.CaseRequestConflictError):
        module.case_id_for_matching_request(existing, payload(**changes))
    assert existing == {"request_payload": payload(), "case": {"case_id": "OLD-CASE"}}
