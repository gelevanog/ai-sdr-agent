"""Small, dependency-free metrics: precision / recall / F1, confusion matrices, percentiles."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence


def prf(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def confusion(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> dict[str, dict[str, int]]:
    matrix = {g: dict.fromkeys(labels, 0) for g in labels}
    for g, p in zip(gold, pred, strict=True):
        if g in matrix and p in matrix[g]:
            matrix[g][p] += 1
    return matrix


def per_class(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> dict[str, dict[str, float | int]]:
    out = {}
    for label in labels:
        tp = sum(1 for g, p in zip(gold, pred, strict=True) if g == label and p == label)
        fp = sum(1 for g, p in zip(gold, pred, strict=True) if g != label and p == label)
        fn = sum(1 for g, p in zip(gold, pred, strict=True) if g == label and p != label)
        out[label] = {**prf(tp, fp, fn), "support": sum(1 for g in gold if g == label)}
    return out


def accuracy(gold: Sequence[str], pred: Sequence[str]) -> float:
    return round(sum(1 for g, p in zip(gold, pred, strict=True) if g == p) / len(gold), 4) if gold else 0.0


def macro_f1(table: dict[str, dict[str, float | int]]) -> float:
    values = [float(v["f1"]) for v in table.values() if int(v["support"]) > 0]
    return round(sum(values) / len(values), 4) if values else 0.0


def percentile(values: Iterable[float], q: float) -> float:
    data = sorted(values)
    if not data:
        return 0.0
    k = (len(data) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(data[lo] + (data[hi] - data[lo]) * (k - lo), 2)


def counts(values: Iterable[str]) -> dict[str, int]:
    return dict(Counter(values).most_common())


def rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
