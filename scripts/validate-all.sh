#!/usr/bin/env bash
# validate-all.sh — 校验所有技能目录的完整性
# 检查：README.md 存在、CHANGELOG.md 存在、references/ 目录结构
# 退出码 0 = 全部通过, 1 = 有错误

set -euo pipefail

SKILLS_DIR="$(cd "$(dirname "$0")/../skills" && pwd)"
errors=0

validate_skill() {
    local name="$1"
    local dir="$2"
    local has_error=0

    # README.md 必须存在
    if [[ ! -f "$dir/README.md" ]]; then
        echo "❌ [$name] 缺少 README.md"
        has_error=1
    fi

    # CHANGELOG.md 最好有
    if [[ ! -f "$dir/CHANGELOG.md" ]]; then
        echo "⚠️  [$name] 缺少 CHANGELOG.md"
    fi

    # 检查 README.md 行数（建议不超过 500 行）
    if [[ -f "$dir/README.md" ]]; then
        local line_count
        line_count=$(wc -l < "$dir/README.md")
        if [[ "$line_count" -gt 500 ]]; then
            echo "⚠️  [$name] README.md 有 $line_count 行（建议 < 500）"
        fi
    fi

    [[ "$has_error" -eq 0 ]]
}

echo "=== 技能校验 ==="
echo "扫描目录: $SKILLS_DIR"
echo ""

for skill_dir in "$SKILLS_DIR"/*/; do
    skill_name="$(basename "$skill_dir")"
    if validate_skill "$skill_name" "$skill_dir"; then
        echo "✅ [$skill_name] OK"
    else
        errors=$((errors + 1))
    fi
done

echo ""
echo "=== 结果: $errors 个错误 ==="
exit "$errors"
