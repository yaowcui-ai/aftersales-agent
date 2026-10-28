"""Upgrade evaluation tests: expectations are separate from executed inputs."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "upgrade_evaluator", ROOT / "scripts/evaluate_upgrade.py"
)


def evaluator():
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def test_frozen_catalog_has_independent_conditions_and_three_splits():
    module = evaluator()
    cases = module.load_cases()
    assert len(cases) >= 120
    assert {c["split"] for c in cases} == {"dev", "validation", "test"}
    assert len({module.condition_signature(c) for c in cases}) == len(cases)
    assert module.verify_manifest(cases)["valid"]


def test_language_inputs_match_structured_read_and_form_requests():
    module = evaluator()
    cases = module.load_cases()
    assert module.validate_language_inputs(cases) == []
    import copy

    conflict = copy.deepcopy(next(c for c in cases if c["condition"] == "read_list_total"))
    conflict["user_turns"] = ["请处理ORDER-A退款10元"]
    assert module.validate_language_inputs([conflict])


def test_v2_preserves_v1_expectations_and_condition_inputs():
    module = evaluator()
    import json

    old = json.loads(
        (ROOT / "evaluation/upgrade/catalogs/v1/cases.json").read_text(encoding="utf-8")
    )
    new = module.load_cases()
    assert len(old) == len(new)
    for prior, current in zip(old, new):
        for key in [
            "case_id",
            "initial_state",
            "input_steps",
            "expected_db",
            "expected_tools",
            "expected_errors",
            "forbidden_actions",
            "split",
        ]:
            assert prior[key] == current[key]
    assert next(c for c in new if c["case_id"] == "U045")["user_turns"] == [
        "请查询我的订单总数和订单列表。"
    ]


def test_changed_expectation_does_not_change_executed_plan():
    module = evaluator()
    import copy

    case = module.load_cases()[0]
    altered = copy.deepcopy(case)
    altered["expected_db"]["refund_count"] = 999
    assert module.input_plan(case) == module.input_plan(altered)


def test_missing_usage_is_unknown_and_budget_hard_caps():
    module = evaluator()
    budget = module.ModelBudget(max_calls=2, max_tokens=120000)
    budget.reserve(100)
    budget.record(None)
    budget.reserve(100)
    budget.record({"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
    with pytest.raises(module.BudgetExceeded):
        budget.reserve(100)
    assert budget.summary()["total_tokens"] is None
    assert budget.summary()["reported_total_tokens"] == 12
    assert budget.summary()["http_calls"] == 2


@pytest.mark.asyncio
async def test_actual_database_and_tool_parameters_determine_success():
    module = evaluator()
    case = next(c for c in module.load_cases() if c["condition"] == "refund_paid_partial")
    result = await module.run_case(case)
    assert result["passed"], result["checks"]
    assert result["actual_db"]["refund_count"] == 1
    assert result["actual_db"]["order"]["refunded_cents"] == 1000
    assert result["initial_db"]["refund_count"] == 0
    assert result["model"]["http_calls"] == 0
    import copy

    wrong = copy.deepcopy(case)
    wrong["expected_db"]["refund_count"] = 2
    result = await module.run_case(wrong)
    assert not result["passed"]


@pytest.mark.asyncio
async def test_return_authorization_does_not_pay_until_admin_receives():
    module = evaluator()
    cases = module.load_cases()
    waiting = next(c for c in cases if c["condition"] == "return_shipped_wait_receipt")
    completed = next(c for c in cases if c["condition"] == "return_delivered_admin_receive")
    first, second = await module.run_case(waiting), await module.run_case(completed)
    assert first["passed"], first["checks"]
    assert second["passed"], second["checks"]
    assert first["actual_db"]["refund_count"] == 0
    assert second["actual_db"]["refund_count"] == 1


def test_default_sampling_is_limited_and_repeats_are_not_independent_cases():
    module = evaluator()
    cases = module.load_cases()
    selected = module.select_cases(cases, limit=12)
    assert len(selected) == 12
    assert len({c["category"] for c in selected}) >= 8
    summary = module.summarize(
        [
            {
                "case_id": "one",
                "passed": True,
                "latency_ms": x,
                "trace": [],
                "checks": [],
                "model": {"http_calls": 0, "total_tokens": 0},
                "human_review": False,
                "security_blocked": False,
                "rounds": 1,
            }
            for x in [1, 2, 3]
        ]
    )
    assert summary["independent_cases"] == 1
    assert summary["runs"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline", ["B1", "B2", "B3"])
async def test_model_ablations_execute_real_database_with_explicit_mock_label(baseline):
    module = evaluator()
    from upgrade_eval.models import ScriptedModel

    case = next(c for c in module.load_cases() if c["condition"] == "refund_paid_partial")
    result = await module.run_case(case, baseline=baseline, model=ScriptedModel())
    assert result["passed"], [c for c in result["checks"] if not c["passed"]]
    assert result["evidence_type"] == "scripted_mock_model"
    assert result["actual_db"]["refund_count"] == 1
    assert result["model"]["http_calls"] == 0
    if baseline == "B3":
        assert result["model"]["graph_resumed"] is True


def test_manifest_detects_mutated_catalog(tmp_path):
    module = evaluator()
    frozen = module.load_cases()
    path = tmp_path / "cases.json"
    import json

    altered = json.loads(json.dumps(frozen))
    altered[0]["expected_db"]["refund_count"] = 200
    path.write_text(json.dumps(altered), encoding="utf-8")
    assert module.verify_manifest(altered, path)["valid"] is False


@pytest.mark.asyncio
async def test_live_http_failure_counts_call_and_marks_usage_unknown(monkeypatch):
    module = evaluator()
    import httpx
    from upgrade_eval.models import LiveModel

    import aftersales_delivery.config as config

    monkeypatch.setattr(
        config,
        "project_values",
        lambda: {
            "COMPATIBLE_API_KEY": "test-only",
            "COMPATIBLE_BASE_URL": "https://invalid.test/v1",
        },
    )

    async def failed_post(self, *args, **kwargs):
        return httpx.Response(503)

    monkeypatch.setattr(httpx.AsyncClient, "post", failed_post)
    model = LiveModel(module.ModelBudget(max_calls=1))
    with pytest.raises(RuntimeError, match="MODEL_HTTP_503"):
        await model([], [])
    assert model.budget.summary()["http_calls"] == 1
    assert model.budget.summary()["total_tokens"] is None
    with pytest.raises(module.BudgetExceeded):
        await model([], [])


@pytest.mark.asyncio
async def test_model_scoring_allows_safe_reads_but_rejects_wrong_money():
    module = evaluator()
    from upgrade_eval.models import ScriptedModel

    case = next(c for c in module.load_cases() if c["condition"] == "refund_paid_partial")

    class ExtraReadModel(ScriptedModel):
        def reset(self, value):
            super().reset(value)
            self.steps.insert(0, {"tool": "get_order", "args": {"order_id": "ORDER-A"}})

    good = await module.run_case(case, baseline="B2", model=ExtraReadModel())
    assert good["passed"], good["checks"]

    class WrongAmountModel(ScriptedModel):
        def reset(self, value):
            super().reset(value)
            self.steps[0]["args"]["amount"] = "99.00"

    bad = await module.run_case(case, baseline="B2", model=WrongAmountModel())
    assert bad["passed"] is False
    assert bad["actual_db"]["order"]["refunded_cents"] == 9900


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline", ["B1", "B2", "B3"])
async def test_fair_baselines_can_observe_then_request_with_same_call_cap(baseline):
    module = evaluator()
    from upgrade_eval.models import ScriptedModel

    case = next(c for c in module.load_cases() if c["condition"] == "refund_paid_partial")

    class ObserveThenRequest(ScriptedModel):
        def reset(self, value):
            super().reset(value)
            self.steps.insert(0, {"tool": "get_order", "args": {"order_id": "ORDER-A"}})

    result = await module.run_case(case, baseline=baseline, model=ObserveThenRequest())
    assert result["passed"]
    assert result["model"]["scripted_calls"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline", ["B1", "B2", "B3"])
async def test_fair_baselines_stop_at_six_model_calls(baseline):
    module = evaluator()
    from langchain_core.messages import AIMessage
    from upgrade_eval.models import ScriptedModel

    case = next(c for c in module.load_cases() if c["condition"] == "refund_paid_partial")

    class NeverFinishes(ScriptedModel):
        async def __call__(self, messages, tools):
            self.calls += 1
            return AIMessage(
                content="",
                tool_calls=[
                    {"id": str(self.calls), "name": "get_order", "args": {"order_id": "ORDER-A"}}
                ],
            )

    model = NeverFinishes()
    result = await module.run_case(case, baseline=baseline, model=model)
    assert model.calls == 6
    assert not result["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline", ["B1", "B2", "B3"])
async def test_budget_stop_preserves_partial_trace_and_actual_database(baseline):
    module = evaluator()
    from langchain_core.messages import AIMessage
    from upgrade_eval.models import ScriptedModel

    case = next(c for c in module.load_cases() if c["condition"] == "refund_paid_partial")

    class ExhaustedAfterRead(ScriptedModel):
        def __init__(self):
            super().__init__()
            self.budget = module.ModelBudget(max_calls=1)

        async def __call__(self, messages, tools):
            self.budget.reserve(100)
            self.calls += 1
            self.budget.record(None)
            return AIMessage(
                content="",
                tool_calls=[{"id": "read", "name": "get_order", "args": {"order_id": "ORDER-A"}}],
            )

    result = await module.run_case(case, baseline=baseline, mode="live", model=ExhaustedAfterRead())
    assert result["stop_reason"] == "MODEL_BUDGET_EXHAUSTED"
    assert result["passed"] is False
    assert result["actual_db"]["refund_count"] == 0
    assert result["trace"][0]["tool"] == "get_order"
    assert result["model"]["http_calls"] == 1
    assert result["model"]["total_tokens"] is None
