# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""User-defined modules and a shared 001..999 sequence, independent of part category."""
import re

SIMPLE_TYPE = '通用编号'


def module_code(value):
    code = str(value or '').strip().upper()
    return code.zfill(2) if code.isdigit() else code


def validate_module(value):
    code = module_code(value)
    if not re.fullmatch(r'[A-Z0-9]{1,12}', code):
        raise ValueError('模块代号请填写1至12位字母或数字，例如 DJ、JJ、SL。')
    return code


def full_name(code, module, sequence, note):
    return f'{code}-{module_code(module)}-{sequence:03d}-{note.strip()}'


def choose_sequence(connection, project_id, module, requested=None):
    module = validate_module(module)
    used = {r[0] for r in connection.execute('''
        SELECT part_sequence FROM drawing_records WHERE project_id=? AND module_code=?
        UNION SELECT part_sequence FROM number_reservations WHERE project_id=? AND module_code=? AND status<>'released'
        UNION SELECT part_sequence FROM solidworks_operations WHERE project_id=? AND module_code=? AND status<>'failed'
    ''', (project_id, module) * 3)}
    counter = connection.execute('SELECT next_sequence FROM part_sequence_counters WHERE project_id=? AND module_code=? AND part_type=?', (project_id, module, SIMPLE_TYPE)).fetchone()
    number = max(max(used, default=0) + 1, counter[0] if counter else 1)
    if requested is not None:
        if isinstance(requested, bool) or not str(requested).isdigit():
            raise ValueError('图号请填写001至999之间的整数。')
        number = int(requested)
    if not 1 <= number <= 999:
        raise ValueError('当前模块图号已用尽，或手动图号不在001至999之间。')
    if number in used:
        raise ValueError(f'图号 {module}-{number:03d} 已使用或已预留，请更换图号。')
    return number
