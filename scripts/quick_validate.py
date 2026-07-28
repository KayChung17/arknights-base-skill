#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md 缺少 YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("SKILL.md frontmatter 未闭合")
    result = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"frontmatter 行格式错误: {line}")
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip('"')
    return result


def validate_skill(path: Path) -> list[str]:
    errors = []
    skill_file = path / "SKILL.md"
    if not skill_file.exists():
        return [f"{path}: 缺少 SKILL.md"]
    try:
        metadata = parse_frontmatter(skill_file)
    except ValueError as exc:
        return [f"{path}: {exc}"]
    if set(metadata) != {"name", "description"}:
        errors.append(f"{path}: frontmatter 只能包含 name 和 description")
    name = metadata.get("name", "")
    if not NAME_RE.fullmatch(name):
        errors.append(f"{path}: name 必须由小写字母、数字和连字符组成，最长64字符")
    if path.name != name:
        errors.append(f"{path}: 文件夹名称应与 name 一致")
    if not metadata.get("description"):
        errors.append(f"{path}: description 不能为空")
    if len(metadata.get("description", "")) < 40:
        errors.append(f"{path}: description 过短，触发语境不完整")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    errors = []
    for raw in args.paths:
        errors.extend(validate_skill(Path(raw)))
    if errors:
        print(f"Skill 校验失败：{len(errors)} 个问题")
        for item in errors:
            print(f"  - {item}")
        return 1
    print(f"Skill 校验通过：{len(args.paths)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
