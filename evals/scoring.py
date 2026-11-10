from dataclasses import dataclass, field


@dataclass
class Score:
    groups_total: int
    groups_hit: int
    files_total: int
    files_hit: int
    missing: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.groups_hit / self.groups_total if self.groups_total else 1.0

    @property
    def passed(self) -> bool:
        files_ok = self.files_total == 0 or self.files_hit > 0
        return self.groups_hit == self.groups_total and files_ok


def score_answer(answer: str, citations: list[str], expect: list[list[str]],
                 files: list[str]) -> Score:
    """Every expect-group needs at least one of its alternatives in the answer text, and at
    least one expected file has to show up among the citations."""
    text = answer.lower()
    hit, missing = 0, []
    for group in expect:
        if any(alt.lower() in text for alt in group):
            hit += 1
        else:
            missing.append(" | ".join(group))
    cited = " ".join(citations).lower()
    files_hit = sum(1 for f in files if f.lower() in cited or f.lower() in text)
    return Score(len(expect), hit, len(files), files_hit, missing)
