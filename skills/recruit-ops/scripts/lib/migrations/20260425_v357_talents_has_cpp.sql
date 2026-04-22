-- 2026-04-25 v3.5.7  intake.cmd_route_interviewer 一面派单需要的「是否会 C++」字段
ALTER TABLE talents ADD COLUMN IF NOT EXISTS has_cpp BOOLEAN;
COMMENT ON COLUMN talents.has_cpp IS
    'v3.5.7: LLM 从 CV 解析的「是否会 C++」。'
    'true=明确写了 C++ 技能或用 C++ 做过项目；'
    'false=明确没提 C++ 或只用其他语言；'
    'NULL=未知/未判断（cmd_parse_cv 返回 null 时直接落地为 NULL）。'
    '由 intake.cmd_route_interviewer 用于 §5.11 一面派单（cpp_first 优先级）。';
