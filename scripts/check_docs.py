#!/usr/bin/env python3
"""Проверка документации и конфигурации CineMatch.

Что проверяется (запуск из корня репозитория):

1) полнота комплекта документов — по 23 документа 010…165 в каждом модуле (у агентов ещё 125);
2) битые ссылки в Markdown;
3) пути в косых кавычках, которых нет в репозитории;
4) ссылки на диаграммы: файл существует, лишних диаграмм нет;
5) файлы нулевого размера;
6) трассировка: требования модуля, не попавшие в матрицу 095;
7) расхождение переменных окружения .env.example и документов.

Запуск:  python scripts/check_docs.py
Код возврата 1 — есть расхождения, 0 — их нет.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODULES = ("agents", "data", "ui")
EXPECTED = "010 020 030 040 050 060 070 075 080 090 095 110 115 120 130 135 140 145 146 150 155 160 165".split()
AGENT_EXTRA = {"125"}
ENV_RE = re.compile(r"`((?:LLM|TMDB|CINEMATCH|AGENT|TELEGRAM|CLI|TURN)_[A-Z_]+)`")

problems = 0


def report(kind: str, items: list[str]) -> None:
    global problems
    if items:
        problems += 1
        print(f"[!] {kind}: {len(items)}")
        for item in items[:40]:
            print("    " + item)
    else:
        print(f"[+] {kind}: нет")


def python_files() -> list[str]:
    """Файлы проверяемого комплекта: documents/, docs/ и README.md."""
    paths = []
    for folder in (os.path.join(ROOT, "documents"), os.path.join(ROOT, "docs")):
        for base, _dirs, files in os.walk(folder):
            paths += [os.path.join(base, f) for f in files if f.endswith(".md")]
    readme = os.path.join(ROOT, "README.md")
    if os.path.exists(readme):
        paths.append(readme)
    return paths


def read(path: str) -> str:
    return open(path, encoding="utf-8", errors="ignore").read()


def expand(text: str, prefix: str) -> set[str]:
    """Развернуть записи 'NFR-AG-01…03, 10' в набор идентификаторов."""
    ids: set[str] = set()
    for m in re.finditer(r"(?<![A-Z-])" + re.escape(prefix) + r"-(\d+)([^\n|]*)", text):
        first = int(m.group(1))
        ids.add(f"{prefix}-{first:02d}")
        tail = m.group(2)
        for r in re.finditer(r"(?:…|\.\.\.|–|—)\s*(\d+)", tail):
            end = int(r.group(1))
            ids |= {f"{prefix}-{n:02d}" for n in range(first, end + 1)}
        for c in re.finditer(r",\s*(\d+)(?!\s*(?:…|\.\.\.|–))", tail):
            ids.add(f"{prefix}-{int(c.group(1)):02d}")
    return ids


def check_documents() -> None:
    missing = []
    for module in MODULES:
        folder = os.path.join(ROOT, "documents", module)
        names = [n for n in os.listdir(folder) if n.endswith(".md") and n != "README.md"]
        numbers = {n.split(".")[0] for n in names}
        allowed = set(EXPECTED)
        if module == "agents":
            allowed |= AGENT_EXTRA
        for num in EXPECTED:
            if num not in numbers:
                missing.append(f"{module}: нет документа {num}")
        for num in sorted(numbers - allowed):
            missing.append(f"{module}: лишний документ {num}")
    report("полнота комплекта документов (по 23 в модуле)", missing)


def check_links() -> None:
    broken, stale, empty = [], [], []
    for path in python_files():
        rel = os.path.relpath(path, ROOT)
        if os.path.getsize(path) == 0:
            empty.append(rel)
        text = read(path)
        for line_no, line in enumerate(text.splitlines(), 1):
            for target in re.findall(r"\]\(([^)\s]+)", line):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                clean = target.split("#")[0]
                candidates = [
                    os.path.normpath(os.path.join(os.path.dirname(path), clean)),
                    os.path.normpath(os.path.join(ROOT, clean)),
                ]
                if not any(os.path.exists(c) for c in candidates):
                    broken.append(f"{rel}:{line_no} -> {target}")
            for target in re.findall(r"`([\w./-]+\.(?:md|puml|svg|png|json|ya?ml|py|ts|tsx|html))`", line):
                if "/" not in target or target.startswith(("cinematch/", "migrations/", "tests/", "scripts/")) or "YYYY" in target:
                    continue
                candidates = [
                    os.path.normpath(os.path.join(os.path.dirname(path), target)),
                    os.path.normpath(os.path.join(ROOT, target)),
                ]
                if not any(os.path.exists(c) for c in candidates):
                    stale.append(f"{rel}:{line_no} -> {target}")
    report("битые ссылки в Markdown", broken)
    report("пути в косых кавычках, которых нет", stale)
    report("файлы нулевого размера", empty)


def check_diagrams() -> None:
    used, existing = set(), set()
    for module in MODULES:
        folder = os.path.join(ROOT, "documents", module, "diagrams")
        if not os.path.isdir(folder):
            continue
        existing |= {os.path.join(folder, n) for n in os.listdir(folder)}
    for path in python_files():
        if "documents" not in path:
            continue
        for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", read(path)):
            used.add(os.path.normpath(os.path.join(os.path.dirname(path), target)))
    missing = [os.path.relpath(u, ROOT) for u in used if not os.path.exists(u)]
    unused = [
        os.path.relpath(f, ROOT)
        for f in existing
        if f.endswith(".svg") and os.path.normpath(f) not in used
    ]
    nosvg = []
    for f in existing:
        if f.endswith(".puml") and f[:-5] + ".svg" not in existing:
            nosvg.append(os.path.relpath(f, ROOT))
    report("ссылки на отсутствующие диаграммы", missing)
    report("диаграммы, не упомянутые в документах", unused)
    report("исходники PlantUML без изображения SVG", nosvg)


def check_traceability() -> None:
    missing = []
    for module in MODULES:
        folder = os.path.join(ROOT, "documents", module)
        matrix = read(os.path.join(folder, "095.requirements-check.md"))
        source = read(os.path.join(folder, "075.nfr.md")) + "\n" + matrix
        defined = set(re.findall(r"(?<![A-Z-])((?:FR|NFR)-[A-Z]+-\d+)", source))
        covered = set(re.findall(r"(?<![A-Z-])((?:FR|NFR)-[A-Z]+-\d+)", matrix))
        gap = sorted(defined - covered)
        if gap:
            missing.append(f"{module}: требования вне матрицы — {', '.join(gap)}")
    report("трассировка: требования вне матрицы 095", missing)


def check_env() -> None:
    example = os.path.join(ROOT, ".env.example")
    if not os.path.exists(example):
        report("переменные окружения", [".env.example не найден"])
        return
    in_example = set(re.findall(r"(?m)^([A-Z_]+)=", read(example)))
    non_config = {"LLM_UNAVAILABLE", "LLM_INVALID_OUTPUT", "DATA_UNAVAILABLE", "AGENT_TIMEOUT", "SESSION_BUSY", "CATALOG_EMPTY", "INPUT_TOO_LONG", "INVALID_INPUT", "LIMIT_EXCEEDED", "CITY_NOT_FOUND", "LLM_MODE", "LLM_PROVIDER", "TELEGRAM_BOT_TOKEN", "CLI_USER_ID"}
    docs_folder = os.path.join(ROOT, "documents")
    in_docs = set()
    for module in MODULES:
        folder = os.path.join(docs_folder, module)
        for name in os.listdir(folder):
            if name.endswith(".md"):
                in_docs |= set(ENV_RE.findall(read(os.path.join(folder, name))))
    in_docs -= non_config
    items = [f"только в документах: {v}" for v in sorted(in_docs - in_example)]
    items += [f"только в .env.example: {v}" for v in sorted(in_example - in_docs)]
    report("переменные окружения: документы против .env.example", items)


def main() -> int:
    print("Проверка документации CineMatch\n" + "=" * 40)
    check_documents()
    check_links()
    check_diagrams()
    check_traceability()
    check_env()
    print("=" * 40)
    print("ИТОГ:", "есть расхождения — см. пометки [!]" if problems else "расхождений нет")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
