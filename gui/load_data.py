"""Load export naming and CSV reading, independent of the GUI."""
from __future__ import annotations

from gpp3323.i18n import tr

import csv
import math
import re
from datetime import datetime
from pathlib import Path

from gui.monitor_tab import Sample

COLUMNS = ['timestamp', 'elapsed_s', 'channel', 'voltage_V', 'current_A', 'power_W']
EXPORT_COLUMNS = [*COLUMNS, 'power_calculated_W']


def validate_name(name: str) -> str:
    if (not name or name != name.strip() or name.endswith('.') or len(name) > 180
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name in ('.', '..')
            or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name)):
        raise ValueError(tr('名稱不可空白、超過 180 字元，或包含 Windows 不允許的字元／保留名稱。'))
    return name


def chinese_number(text: str) -> int:
    if text.isdecimal():
        return int(text)
    digits = dict(zip('零〇一二三四五六七八九兩', [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2]))
    total = number = 0
    for char in text:
        if char in digits:
            number = digits[char]
        else:
            total += (number or 1) * {'十': 10, '百': 100, '千': 1000}[char]
            number = 0
    return total + number


def suggested_name(notes: str, timestamp: datetime) -> str:
    run_pattern = r'(?i)\b(\d+)(?:st|nd|rd|th)?\s*run\b|第\s*([零〇一二三四五六七八九十百千兩\d]+)\s*次'
    run = re.search(run_pattern, notes)
    count = max(1, chinese_number(run.group(1) or run.group(2))) if run else 1
    remaining = re.sub(run_pattern, '', notes)
    current = re.search(r'(?i)(\d+(?:\.\d+)?)\s*(mA|A)\b', remaining)
    current_text = ''
    if current:
        current_text = f"{current.group(1)}{'mA' if current.group(2).lower() == 'ma' else 'A'}"
        remaining = remaining[:current.start()] + remaining[current.end():]
    remaining = re.sub(r'(?i)(?:\bno\.?\s*|#)\s*(\d+)', r'no\1', remaining)
    remaining = re.sub(r'(?i)\b(?:load|at)\b', '', remaining)
    remaining = re.sub(r'[<>:"/\\|?*\x00-\x1f,@;\s]+', '_', remaining).strip('_.-')
    prefix = remaining or 'LoadTest'
    if current_text:
        prefix += '_' + current_text
    return f'{prefix[:135]}-{count}-{timestamp:%Y%m%d_%H%M%S}'


def export_dataset(parent: Path, name: str, samples: list[Sample], notes: str) -> Path:
    validate_name(name)
    directory = parent / name
    suffix = 2
    while True:
        try:
            directory.mkdir()
            break
        except FileExistsError:
            directory = parent / f'{name}_{suffix}'
            suffix += 1
    try:
        with (directory / 'measurements.csv').open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.writer(stream)
            writer.writerow(EXPORT_COLUMNS)
            for sample in samples:
                writer.writerow([sample.timestamp.isoformat(timespec='milliseconds'),
                                 f'{sample.elapsed:.3f}', sample.channel,
                                 f'{sample.voltage:.6f}', f'{sample.current:.6f}', f'{sample.power:.6f}',
                                 f'{sample.calculated_power:.12g}'])
        (directory / 'notes.txt').write_text(notes, encoding='utf-8-sig')
    except OSError:
        for filename in ('measurements.csv', 'notes.txt'):
            (directory / filename).unlink(missing_ok=True)
        directory.rmdir()
        raise
    return directory


def read_samples(path: Path) -> list[Sample]:
    result = []
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not set(COLUMNS).issubset(reader.fieldnames or []):
            raise ValueError(tr('缺少必要的量測 CSV 欄位'))
        for line, row in enumerate(reader, 2):
            try:
                values = [float(row[key]) for key in ('elapsed_s', 'voltage_V', 'current_A', 'power_W')]
                if not all(math.isfinite(value) for value in values) or values[0] < 0:
                    raise ValueError(tr('非有效數值'))
                result.append(Sample(datetime.fromisoformat(row['timestamp']), values[0],
                                     int(row['channel']), *values[1:]))
            except (ValueError, TypeError) as exc:
                raise ValueError(tr('第 {0} 行資料無效：{1}', f'{line}', f'{exc}')) from exc
    if not result:
        raise ValueError(tr('CSV 沒有量測資料'))
    return sorted(result, key=lambda sample: sample.elapsed)


def rename_dataset(path: Path, name: str) -> Path:
    validate_name(name)
    target = path.with_name(name)
    if target == path:
        return path
    if target.exists():
        raise ValueError(tr('已有同名資料夾，請使用不同名稱。'))
    path.rename(target)
    return target
