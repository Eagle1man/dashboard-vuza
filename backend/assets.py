# -*- coding: utf-8 -*-
"""Версия статики в ссылках на CSS.

Зачем. Браузер запоминает `style.css` и после правки может показать
старую копию: человек нажимает F5, а видит прежний вид и решает, что
ничего не изменилось. Параметр `?v=<дата изменения файла>` делает URL
новым вместе с содержимым — показывать старое становится нечем.

Почему дата берётся у файла, а не проставляется руками: ручную версию
забывают поднять, и это выясняется на приёмке. Здесь источник правды —
сам файл, ошибиться негде.

Запуск:
    python -m backend.assets          обновить версию во всех HTML
    python -m backend.assets --check  только показать, совпадает ли
"""
import re
import sys
import time
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
CSS = WEB / "css" / "style.css"

# href="css/style.css" или href="css/style.css?v=..." — оба варианта.
_LINK = re.compile(r'(href=["\'])css/style\.css(?:\?v=[^"\']*)?(["\'])')


def css_version() -> str:
    """Версия = время изменения style.css, до минут."""
    if not CSS.exists():
        return "0"
    return time.strftime("%Y%m%d-%H%M", time.localtime(CSS.stat().st_mtime))


def stamp(version: str = None, dry_run: bool = False):
    """Проставляет версию во всех HTML рядом с web/. Возвращает (файлы, версия)."""
    version = version or css_version()
    changed = []
    for page in sorted(WEB.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        new = _LINK.sub(r"\1css/style.css?v=" + version + r"\2", text)
        if new != text:
            changed.append(page.name)
            if not dry_run:
                page.write_text(new, encoding="utf-8")
    return changed, version


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    check = "--check" in argv
    changed, version = stamp(dry_run=check)
    if check:
        if changed:
            print("Версия устарела: " + ", ".join(changed))
            print("Обновите: python -m backend.assets")
            return 1
        print(f"Версия актуальна: {version}")
        return 0
    if changed:
        print(f"Версия {version} проставлена: " + ", ".join(changed))
    else:
        print(f"Версия уже актуальна: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
