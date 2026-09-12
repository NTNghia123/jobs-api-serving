"""Quality checks trước publish — cổng chặn (fail → KHÔNG publish, pointer giữ nguyên).

Invariant 9 (plan) + nhất quán nội bộ:
  - reconciliation: với mỗi source, silver + quarantine == số job Mongo đọc lúc extract.
  - silver: COUNT(*) == COUNT(DISTINCT job_id)  (1 job = 1 dòng).
  - batch_id: mọi dòng silver/gold/quarantine mang đúng batch_id đang publish.
  - gold: posting_count ≥ salary_disclosed_count ≥ salary_sample_count (mọi dòng).
  - gold: khoá (batch_id, window, dimension, dimension_value) DUY NHẤT.
  - gold: sample_count == 0  ⇔  median IS NULL (k-anon che ở API, KHÔNG ở gold).

Thuần, không I/O. Executor (2.6d) gọi `run_quality_checks`; `.ok=False` → abort.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.elt.serving.gold import GoldMetricRow
from app.elt.serving.silver import QuarantineRecord, SilverRow


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class QualityReport:
    results: list[CheckResult]

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]

    def summary(self) -> str:
        mark = "OK" if self.ok else "FAIL"
        lines = [f"[{mark}] quality checks ({len(self.failures)}/{len(self.results)} fail)"]
        for r in self.results:
            lines.append(f"  {'✓' if r.passed else '✗'} {r.name}" + (f" — {r.detail}" if r.detail else ""))
        return "\n".join(lines)


def check_reconciliation(
    silver: Sequence[SilverRow],
    quarantine: Sequence[QuarantineRecord],
    extract_counts: dict[str, int],
) -> CheckResult:
    s = Counter(r.source for r in silver)
    q = Counter(r.source for r in quarantine)
    bad = []
    for src, n in extract_counts.items():
        got = s.get(src, 0) + q.get(src, 0)
        if got != n:
            bad.append(f"{src}: silver+quar={got} ≠ extract={n}")
    # nguồn xuất hiện trong kết quả nhưng không có trong extract_counts → cũng là lệch
    for src in set(s) | set(q):
        if src not in extract_counts:
            bad.append(f"{src}: có kết quả nhưng không có trong extract_counts")
    return CheckResult("reconciliation_source==silver+quarantine", not bad, "; ".join(bad))


def check_silver_distinct(silver: Sequence[SilverRow]) -> CheckResult:
    n, d = len(silver), len({r.job_id for r in silver})
    return CheckResult("silver_count==distinct_job_id", n == d, f"count={n} distinct={d}")


def check_batch_id(
    silver: Sequence[SilverRow],
    gold: Sequence[GoldMetricRow],
    quarantine: Sequence[QuarantineRecord],
    batch_id: str,
) -> CheckResult:
    bad = sum(1 for r in silver if r.batch_id != batch_id)
    bad += sum(1 for r in gold if r.batch_id != batch_id)
    bad += sum(1 for r in quarantine if r.batch_id != batch_id)
    return CheckResult("rows_carry_batch_id", bad == 0, f"{bad} dòng sai batch_id")


def check_gold_count_ordering(gold: Sequence[GoldMetricRow]) -> CheckResult:
    bad = [
        f"{g.dimension}/{g.dimension_value}/{g.window}"
        for g in gold
        if not (g.posting_count >= g.salary_disclosed_count >= g.salary_sample_count)
    ]
    return CheckResult("gold_posting>=disclosed>=sample", not bad, f"{len(bad)} vi phạm: {bad[:5]}")


def check_gold_uniqueness(gold: Sequence[GoldMetricRow]) -> CheckResult:
    keys = Counter((g.batch_id, g.window, g.dimension, g.dimension_value) for g in gold)
    dups = [k for k, c in keys.items() if c > 1]
    return CheckResult("gold_key_unique", not dups, f"{len(dups)} khoá trùng: {dups[:5]}")


def check_gold_median_consistency(gold: Sequence[GoldMetricRow]) -> CheckResult:
    bad = [
        f"{g.dimension_value}/{g.window}"
        for g in gold
        if (g.salary_sample_count == 0) != (g.median_salary_vnd_month is None)
    ]
    return CheckResult("gold_median_null_iff_sample_zero", not bad, f"{len(bad)} vi phạm: {bad[:5]}")


def run_quality_checks(
    silver: Sequence[SilverRow],
    quarantine: Sequence[QuarantineRecord],
    gold: Sequence[GoldMetricRow],
    batch_id: str,
    extract_counts: dict[str, int],
) -> QualityReport:
    return QualityReport([
        check_reconciliation(silver, quarantine, extract_counts),
        check_silver_distinct(silver),
        check_batch_id(silver, gold, quarantine, batch_id),
        check_gold_count_ordering(gold),
        check_gold_uniqueness(gold),
        check_gold_median_consistency(gold),
    ])
