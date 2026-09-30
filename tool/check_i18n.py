#!/usr/bin/env python3
"""Detect "filler" translations in the Luotopia ARB files.

Two classes of filler are forbidden:

1. English filler — a non-Chinese locale (ja/ko/es/fr/vi/pt/ru) whose value is
   a verbatim copy of the English template (en.arb).
2. Simplified-Chinese filler — a Traditional locale (zh_Hant / yue_Hant) whose
   value is a verbatim copy of the Simplified reference (zh.arb) and contains
   simplified-only glyphs. yue_Hans (Cantonese in simplified script) is not
   checked: formal written Cantonese legitimately uses Standard Written
   Chinese, so equality with Mandarin is not, by itself, filler.

A third, structural check runs alongside them: `"@key": {}` metadata blocks
that carry no description and no placeholders. See check_empty_metadata.

Some values legitimately keep the same spelling everywhere: the Latin loanwords
and technical terms listed in KEEP_VALUES (mirrored from the ``@@keep-english``
policy note), brand names, currency codes, units, OS names, build channels,
URLs, and placeholder-only format strings. A value in that set is not flagged;
everything else must be translated.

Usage:
    check_i18n.py            # check every locale
    check_i18n.py --staged   # check only keys added/changed in the index
                             # (what the pre-commit hook runs)
    check_i18n.py --fix      # strip empty "@key": {} blocks from every catalog

Exit code: 0 clean, 1 filler found.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ARB_DIR = Path(__file__).resolve().parent.parent

TEMPLATE = "en"
SIMPLIFIED = "zh"
NON_CHINESE_LOCALES = ["ja", "ko", "es", "fr", "vi", "pt", "ru"]
CHINESE_LOCALES = ["zh_Hant", "yue_Hant"]

# Values whose English spelling is correct in every locale. Mirrors the
# ``@@keep-english`` note: Latin loanwords / technical terms, brand names,
# currency codes, units, OS names, build channels, URLs, and format-only
# templates. Kept as exact strings so the check never guesses.
KEEP_VALUES = {
    # @@keep-english loanwords / technical terms
    "Total", "Original", "Manual", "Campus", "Audio", "Info", "Distance",
    "Status", "Date", "Version", "Type", "Source", "Forum", "Messages",
    "Notifications", "Session", "Mobile", "Architecture", "Documents",
    "Images", "Karma", "Delta", "Regular", "Error", "General", "Actor",
    "Auto", "Satellite", "Patient", "Instructions", "Minute", "Video",
    "Email", "Sem",
    # App / product / provider brand forms
    "Luotopia", "Shiguang", "Starlink", "Chaoxing", "Zhihui Luojia",
    "Budao Lepao", "Huawei Watch", "MiSans", "Google Noto Sans",
    "WeChat Pay", "Alipay", "UnionPay", "CERNET", "China Telecom",
    "China Unicom", "China Mobile", "Moonshot (Moonshot AI)",
    "Zhipu AI (GLM)", "Baidu Qianfan", "Alibaba Tongyi Qianwen",
    "Tencent Hunyuan", "01.AI (Yi)", "Baichuan AI", "Gemini", "Anthropic",
    "OpenAI Completion", "OpenAI Response", "TinyFish", "Open-Meteo",
    "AccuWeather", "Nominatim", "China Weather Net", "WHU Safe Pay",
    "Little Yellow", "Little Green", "Feng Garden", "Gui Garden",
    "Mei Garden", "WHU", "Wakeup", "Wakeup CSV", "CS易办", "Ham",
    # Acronyms / tokens / units
    "VPN", "AI", "IP", "MAC", "ID", "OK", "MCP", "OpenID", "FAQ", "AED",
    "Passkey", "token", "Top P", "AQI", "PM2.5", "PM10", "CO", "NO₂",
    "SO₂", "O₃", "µg/m³", "GPA", "GP", "WHU GPA", "Lv", "SHA-256",
    "Streamable HTTP", "stts", "Root / jailbreak",
    # OS names / build channels / colors
    "Linux", "macOS", "Windows", "Alpha", "Beta", "Stable", "Indigo",
    # Single-word loanwords / admin & dev terms whose English spelling is
    # correct across the supported languages (kept to avoid false positives).
    "Auth Epoch", "Logs", "Pause", "Bio", "Spam", "Webmail", "QR code",
    "Check-in", "Local", "Cloud", "Message…", "Menu", "Quiz", "Photo",
    "Volume", "Transport", "Description", "Classification", "Publication",
    "Discussion", "Administration", "Target", "Reason", "Updated", "Job",
    "Inactive", "Active", "Correct", "Point", "Escalator", "Permanent",
    "Maximum", "Minimum", "Points", "Cadence", "Distance (km)", "Microphone",
    "Photos", "Assistant", "Conversations", "Important", "No", "—",
    "Appeal", "Appealed", "Decision", "Dismiss", "Impersonation", "Pending",
    "Resolved",
    # URLs
    "https://ca.whu.edu.cn/index.html",
    "https://mcp.example.com/mcp",
    "https://example.com/wallpaper.jpg",
    "ip-api.com",
    # Placeholder-only format strings, units, times, dates and symbols
    "{count} sections", "{count} questions", "{rating} / 5", "{price} CNY", "{amount} CNY",
    "{value} CNY", "{lowPrice}-{highPrice} CNY", "~{meters}m",
    "{minutes} min", "{minute} min", "{hour} h", "Sem {s}", "{value}%",
    "{month}/{day}", "{day} - {endDay}", "{start} - {end}",
    "{from} - {to}", "{building} - {room}", "{type} · {venue}",
    "{condition} · {minTemp}°/{maxTemp}°", "{city} · {source} · {date}",
    "[{covers}] {prompt}", "SHA-256: {checksum}", "GPA: {gpa}",
    "GP {point}", "AQI {value}", "Worker {index}", "Video {index}",
    "Lv{level} · {title}", "Lv{level} · {karma} Karma", "{failed}: {error}",
    "{month} {year}", "{year}", "{weekday}", "Total {value}",
    "{count} messages", "08:00", "10:00", "14:00", "9/22-9/28",
    "15 min", "30 min", "15 minutes", "30 minutes", "~", "–", ", ",
}

# Keys whose Simplified Chinese spelling is also correct in Traditional and
# Cantonese (brand names whose glyphs do not differ between scripts).
KEEP_CHINESE_SAME = {
    "appTitle",
    "campusAppZhihuiLuojia",
}

# Simplified-only glyphs that have a distinct Traditional form. A Traditional
# locale (zh_Hant / yue_Hant) copying the Simplified reference is only flagged
# when one of these is present, so strings whose characters are identical in
# both scripts (e.g. 刷新投稿, 设置) are not false-flagged.
SIMPLIFIED_ONLY = set(
    "们个这说问见发现时开关门电车长让动应还进经结级线钟铁钱纸论议记讲语认识设备试请读调谁该书东华亚么义乡买卖卫历压参双变号叶吗员图处复头实宝审尽层岁广应厂库庆开张强录归当彻径术积织组练给红细绝继绪续网罗聪联胜脑脚脸腾营获蓝药艺苏观觉规视触计订讨证译诗话诸课谈谢谓负账货责质费赚贺资赋购贷贸赶赵达迁过还远违运连迟选逻遗邮邻郑邓银镜闻阅闪闭队阳阴阵页项预领颜题频颗额风飞饮饭饰饱驾驶马鱼鸟龙龟"
)

_META_RE = re.compile(r"^@")


def _load(locale: str) -> dict:
    with (ARB_DIR / f"{locale}.arb").open(encoding="utf-8") as handle:
        return json.load(handle)


def _values(data: dict) -> dict[str, str]:
    return {
        key: value
        for key, value in data.items()
        if isinstance(value, str) and not _META_RE.match(key)
    }


def _is_cjk(value: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in value)


def check_locale(
    locale: str,
    template: dict[str, str],
    simplified: dict[str, str],
    locale_values: dict[str, str],
    keys: set[str] | None,
) -> list[str]:
    violations: list[str] = []
    scope = keys if keys is not None else set(locale_values)

    if locale in CHINESE_LOCALES:
        for key in scope:
            if key not in locale_values or key not in simplified:
                continue
            if key in KEEP_CHINESE_SAME:
                continue
            value = locale_values[key]
            if value != simplified[key] or not _is_cjk(value):
                continue
            # Only flag when a simplified-only glyph is present, so strings
            # whose glyphs are identical in both scripts (全部, 设置) are not
            # false-flagged.
            if not any(ch in SIMPLIFIED_ONLY for ch in value):
                continue
            violations.append(
                f"{locale}.arb: {key} copies Simplified Chinese "
                f"({simplified[key]!r})"
            )
        return violations

    for key in scope:
        if key not in locale_values or key not in template:
            continue
        value = locale_values[key]
        if value == template[key] and value not in KEEP_VALUES:
            violations.append(
                f"{locale}.arb: {key} copies English ({template[key]!r})"
            )
    return violations


def all_locales() -> list[str]:
    """Every catalog present in the ARB directory, so the list cannot drift."""
    return sorted(p.stem for p in ARB_DIR.glob("*.arb"))


def check_empty_metadata(locale: str, data: dict) -> list[str]:
    """Flag `"@key": {}` blocks: metadata entries that carry nothing.

    Weblate's ARB export writes one per message. That is how zh_Hant.arb picked
    up 2704 of them in 49071c1 (2026-09-03, "Translated using Weblate (Chinese
    (Traditional Han script))") and ended up 1.5x the size of every other
    catalog. gen-l10n reads placeholder metadata from the template only, so an
    empty block in a locale file changes no behaviour -- but it buries real
    diffs, inflates the review surface in Weblate, and grows on every sync.
    """
    return [
        f"{locale}.arb: {key} is an empty @-metadata block"
        for key in data
        if key.startswith("@") and data[key] == {}
    ]


_EMPTY_META_RE = re.compile(r'^  "@[^"]+": \{\},$')


def fix_empty_metadata() -> int:
    """Rewrite every catalog without its empty `"@key": {}` blocks.

    One-shot cleanup for a Weblate export that re-injected them (see
    check_empty_metadata). Only empty blocks are removed -- a block carrying a
    description or placeholders is left untouched -- and each rewritten file is
    re-parsed before writing. Writes preserve LF endings, which the ARB
    .gitattributes mandates.
    """
    removed_total = 0
    for p in ARB_DIR.glob("*.arb"):
        raw = p.read_text(encoding="utf-8")
        lines = raw.split("\n")
        kept = [line for line in lines if not _EMPTY_META_RE.match(line)]
        n = len(lines) - len(kept)
        if n == 0:
            continue
        text = "\n".join(kept)
        json.loads(text)  # fail loudly rather than write a broken catalog
        p.write_bytes(text.encode("utf-8"))
        removed_total += n
        print(f"check_i18n: {p.stem}.arb removed {n} empty @-metadata block(s)")
    print(f"check_i18n: removed {removed_total} empty block(s) in total")
    return 0


def staged_keys() -> dict[str, set[str]]:
    """Map locale -> keys added or changed in the index, from the staged diff."""
    result: dict[str, set[str]] = {}
    proc = subprocess.run(
        ["git", "diff", "--cached", "--", "*.arb"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ARB_DIR,
    )
    if proc.returncode != 0:
        print("check_i18n: git diff failed; falling back to full check",
              file=sys.stderr)
        return {}

    current = None
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            if path.endswith(".arb"):
                current = Path(path).stem
                result.setdefault(current, set())
        elif line.startswith("+") and not line.startswith("+++") and current:
            match = re.match(r'\+\s*"([^"]+)"\s*:', line)
            if match:
                result[current].add(match.group(1))
    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    if "--fix" in sys.argv:
        return fix_empty_metadata()

    template = _values(_load(TEMPLATE))
    simplified = _values(_load(SIMPLIFIED))

    staged_mode = "--staged" in sys.argv
    staged = staged_keys() if staged_mode else {}
    if staged_mode and not staged:
        print("check_i18n: no staged ARB changes")
        return 0

    violations: list[str] = []
    for locale in NON_CHINESE_LOCALES + CHINESE_LOCALES:
        keys = staged.get(locale) if staged else None
        if staged and keys is None:
            continue
        violations.extend(
            check_locale(
                locale,
                template,
                simplified,
                _values(_load(locale)),
                keys,
            )
        )

    # Structural, not key-scoped: an empty @-metadata block is noise wherever it
    # sits, and a Weblate export touches every key at once, so scoping this to
    # the staged keys would miss most of the damage. Still limited to the
    # catalogs the commit actually touched, so an unrelated commit cannot be
    # blocked by a file it never opened.
    structural: list[str] = []
    for locale in all_locales():
        if staged_mode and locale not in staged:
            continue
        structural.extend(check_empty_metadata(locale, _load(locale)))

    if violations:
        print("check_i18n: filler translations detected:")
        for line in violations:
            print(f"  {line}")
        print(
            "check_i18n: translate these instead of copying English / "
            "Simplified Chinese (see the @@keep-english note in each ARB)."
        )

    if structural:
        print("check_i18n: empty @-metadata blocks detected:")
        print(f"  {len(structural)} in total; first 10:")
        for line in structural[:10]:
            print(f"  {line}")
        print(
            "check_i18n: drop them. Weblate's ARB export emits one per message; "
            "gen-l10n takes placeholder metadata from the template only."
        )

    if violations or structural:
        return 1

    print("check_i18n: " + ("no filler in staged changes" if staged else "clean"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
