#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEDULE_SKILL="$ROOT_DIR/skills/ark-base-schedule"
UPDATE_SKILL="$ROOT_DIR/skills/update-ark-skills"

python "$ROOT_DIR/scripts/quick_validate.py" "$SCHEDULE_SKILL" "$UPDATE_SKILL"
python "$ROOT_DIR/scripts/repository_check.py"
python "$SCHEDULE_SKILL/scripts/doctor.py" >/dev/null
python "$ROOT_DIR/arkbase.py" doctor >/dev/null
python "$SCHEDULE_SKILL/scripts/validate_data.py"
python -m compileall -q "$ROOT_DIR/skills"
python - <<'PY' "$ROOT_DIR"
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
for path in root.rglob("*.json"):
    json.loads(path.read_text(encoding="utf-8"))
print("JSON 文件解析通过")
PY
python -m unittest discover -s "$SCHEDULE_SKILL/tests" -p "test_*.py"
python -m unittest discover -s "$UPDATE_SKILL/tests" -p "test_*.py"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT


python "$UPDATE_SKILL/scripts/import_owned_skill_table.py" \
  --input "$UPDATE_SKILL/tests/fixtures/owned-skill-table.sample.txt" \
  --existing "$SCHEDULE_SKILL/assets/operator-skills.json" \
  --output "$TMP_DIR/operator-skills.imported.json" \
  --data-version validation-owned-roster \
  --warnings-output "$TMP_DIR/import-warnings.txt" >/dev/null

python - <<'PYDATA' "$TMP_DIR/operator-skills.imported.json" "$TMP_DIR/import-warnings.txt"
import json
import sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
summary = value["import_summary"]
assert summary["source_operator_count"] == 3
assert summary["source_skill_count"] == 6
assert summary["canonical_operator_count"] >= 248
assert summary["warning_count"] == 0
assert Path(sys.argv[2]).read_text(encoding="utf-8") == ""
print("脱敏导入样例通过")
PYDATA


python "$SCHEDULE_SKILL/scripts/drone_model.py" \
  --drones 40 \
  --recovery-bonus 45 \
  --hours 6 \
  --output "$TMP_DIR/drone-calculation.json" >/dev/null

python - <<'PYDRONE' "$TMP_DIR/drone-calculation.json"
import json
import sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert value["recovery"]["rate_per_hour"] == 14.5
assert value["recovery"]["recovered_in_hours"] == 87.0
assert value["acceleration"]["base_minutes_removed"] == 120.0
print("无人机恢复与加速换算通过")
PYDRONE

python "$SCHEDULE_SKILL/scripts/normalize_input.py" \
  --roster "$SCHEDULE_SKILL/samples/sample_干员练度表.txt" \
  --goal "赚钱+搓玉" \
  --online-count 3 \
  --online-times 08:00,14:00,20:00 \
  --preferences '{"priority":"guide_fidelity"}' \
  --output "$TMP_DIR/guide-decision-context.json" >/dev/null

python - <<'PY2' "$TMP_DIR/guide-decision-context.json"
import json
import sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert value["objective"]["layout"] == "342"
assert value["baseline"]["reference_id"] == "guide_342_orundum_3_login"
assert [segment["hours"] for segment in value["segment_template"].values()] == [6.0, 6.0, 12.0]
print("攻略基线与非等长操作区间通过")
PY2

python "$SCHEDULE_SKILL/scripts/compare_to_baseline.py" \
  "$SCHEDULE_SKILL/samples/sample_342_guide_candidate_v4.json" \
  --baseline guide_342_orundum_3_login \
  --output "$TMP_DIR/guide-baseline-comparison.json" >/dev/null

python "$SCHEDULE_SKILL/scripts/normalize_input.py" \
  --roster "$SCHEDULE_SKILL/samples/sample_干员练度表.txt" \
  --goal "赚钱+经验书" \
  --layout 243 \
  --shifts 2 \
  --preferences '{"priority":"balanced"}' \
  --output "$TMP_DIR/decision-context.json" >/dev/null

python "$SCHEDULE_SKILL/scripts/schedule_generator.py" \
  --roster "$SCHEDULE_SKILL/samples/sample_干员练度表.txt" \
  --goal "赚钱+经验书" \
  --shifts 2 \
  --output "$TMP_DIR/candidate-a.json" >/dev/null

python - <<'PY' "$TMP_DIR/candidate-a.json" "$TMP_DIR/candidate-b.json"
import copy
import json
import sys
from pathlib import Path
source = Path(sys.argv[1])
plan = json.loads(source.read_text(encoding="utf-8"))
plan.pop("validation", None)
plan["schema_version"] = 3
plan["plan_id"] = "candidate-a"
plan["title"] = "效率基准候选"
plan["decision"] = {
    "strategy": "优先房间效率",
    "rationale": ["使用备用生成器构造可比较基线"],
    "tradeoffs": ["允许跨班复用"],
    "external_evidence_ids": [],
}
source.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
other = copy.deepcopy(plan)
other["plan_id"] = "candidate-b"
other["title"] = "低人员占用候选"
other["decision"] = {
    "strategy": "减少人员占用",
    "rationale": ["保留一个贸易站空缺用于测试覆盖率比较"],
    "tradeoffs": ["降低房间覆盖率"],
    "external_evidence_ids": [],
}
first_shift = next(iter(other["shifts"].values()))
first_room = next(iter(first_shift["rooms"].values()))
first_room["operators"] = first_room["operators"][:-1]
Path(sys.argv[2]).write_text(json.dumps(other, ensure_ascii=False, indent=2), encoding="utf-8")
PY

for candidate in a b; do
  python "$SCHEDULE_SKILL/scripts/validate_plan.py" \
    "$TMP_DIR/candidate-$candidate.json" \
    --roster "$SCHEDULE_SKILL/samples/sample_干员练度表.txt" \
    --output "$TMP_DIR/candidate-$candidate.validated.json" \
    --json >/dev/null
  python "$SCHEDULE_SKILL/scripts/evaluate_plan.py" \
    "$TMP_DIR/candidate-$candidate.json" \
    --roster "$SCHEDULE_SKILL/samples/sample_干员练度表.txt" \
    --output "$TMP_DIR/candidate-$candidate.evaluation.json" >/dev/null
done

python "$SCHEDULE_SKILL/scripts/compare_plans.py" \
  "$TMP_DIR/candidate-a.evaluation.json" \
  "$TMP_DIR/candidate-b.evaluation.json" \
  --profile balanced \
  --output "$TMP_DIR/comparison.json" >/dev/null

python - <<'PY' "$TMP_DIR/comparison.json"
import json
import sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert value["decision_owner"] == "language_model_with_user_preferences"
assert len(value["ranking"]) == 2
print("模型候选处理链路通过")
PY


python "$SCHEDULE_SKILL/scripts/build_combinations.py" \
  "$SCHEDULE_SKILL/samples/sample_decision_context_v060.json" \
  --top-k 40 \
  --operator-pool-size 14 \
  --output "$TMP_DIR/solver-combinations.json" >/dev/null

python "$SCHEDULE_SKILL/scripts/build_model.py" \
  "$SCHEDULE_SKILL/samples/sample_decision_context_v060.json" \
  "$TMP_DIR/solver-combinations.json" \
  --output "$TMP_DIR/solver-model.json" >/dev/null

python "$SCHEDULE_SKILL/scripts/solve_schedule.py" \
  "$SCHEDULE_SKILL/samples/sample_decision_context_v060.json" \
  --combination-library "$TMP_DIR/solver-combinations.json" \
  --top-solutions 1 \
  --time-limit 30 \
  --output "$TMP_DIR/solver-result.json" \
  --plan-output "$TMP_DIR/solver-candidate.json" >/dev/null

python - <<'PY3' "$TMP_DIR/solver-result.json" "$TMP_DIR/solver-model.json"
import json
import sys
from pathlib import Path
result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
model = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
assert result["result_type"] == "hybrid_schedule_solution"
assert result["candidate_plan"]["plan_status"] == "candidate"
assert result["solver"]["backend"] == "scipy.optimize.milp_highs"
assert result["solver"]["actual_simulation_global_optimality_proven"] is False
assert model["variable_count"] > 0
assert model["constraint_count"] > 0
assert model["drone_allocation_variable_count"] > 0
plan = result["selected_solution"]["simulation"]["drone_plan"]
assert plan["feasible"] is True
assert plan["total_used"] > 0
assert abs(plan["total_used"] + plan["total_wasted"] - plan["total_recovered"]) < 1e-6
print("混合枚举、无人机MILP与复算链路通过")
PY3

python "$SCHEDULE_SKILL/scripts/audit_result.py" \
  "$TMP_DIR/solver-result.json" \
  --output "$TMP_DIR/solver-result.audit.json" \
  --strict-warnings >/dev/null

python "$SCHEDULE_SKILL/scripts/generate_report.py" \
  "$TMP_DIR/solver-result.json" \
  --output "$TMP_DIR/solver-result.report.md" >/dev/null

test -s "$TMP_DIR/solver-result.report.md"
echo "结果审计与中文报告通过"

python "$SCHEDULE_SKILL/scripts/run_project.py" \
  "$ROOT_DIR/examples/configs/快速自检.json" \
  --output-dir "$TMP_DIR/project-smoke" >/dev/null

test -s "$TMP_DIR/project-smoke/result.json"
test -s "$TMP_DIR/project-smoke/report.md"
test -s "$TMP_DIR/project-smoke/audit.json"
test -s "$TMP_DIR/project-smoke/coverage.json"
echo "项目配置端到端自检通过"

if command -v skills-ref >/dev/null 2>&1; then
  skills-ref validate "$SCHEDULE_SKILL"
  skills-ref validate "$UPDATE_SKILL"
fi

echo "全部校验通过"
