"""Read-only SSH discovery and explicit, validated manual connection storage/export."""
from __future__ import annotations

import codecs
import glob
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile
import uuid


_NUMBERS = {'connectTimeout': 'ConnectTimeout', 'serverAliveInterval': 'ServerAliveInterval', 'serverAliveCountMax': 'ServerAliveCountMax'}
_ALIAS = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\Z')
_HOST = re.compile(r'[A-Za-z0-9_\[\]:][A-Za-z0-9_.:\[\]%-]*\Z')


def _include_path(value):
    """Expand only tokens that do not require selecting/evaluating a host."""
    def percent(match):
        token = match.group(1)
        if token == '%':
            return '%'
        if token == 'd':
            return str(Path.home())
        raise ValueError(f'SSH Include 含无法静态解析的标记：%{token}')

    value = re.sub(r'%(.)|%$', lambda match: percent(match), value)

    def environment(match):
        name = match.group(1)
        if not name or name not in os.environ:
            raise ValueError(f'SSH Include 环境变量未定义：{name}')
        return os.environ[name]

    value = re.sub(r'\$\{([^}]*)\}', environment, value)
    if '${' in value or not value:
        raise ValueError('SSH Include 路径含无法解析的环境变量')
    try:
        return Path(value).expanduser()
    except RuntimeError as exc:
        raise ValueError('SSH Include 用户目录无法解析') from exc


def discover_hosts(path=None):
    """Collect literal Host candidates, never evaluate Match/ProxyCommand.

    Relative Includes are rooted at ~/.ssh even for a custom -F file, matching
    OpenSSH user configuration. Repeated/cyclic includes are visited only once.
    """
    root = Path(path).expanduser() if path is not None else Path.home() / '.ssh' / 'config'
    base = Path.home() / '.ssh'
    visited, hosts, seen = set(), [], set()

    def visit(file, depth=0, missing_ok=False):
        if depth > 32:
            raise ValueError('SSH Include 嵌套超过 32 层')
        resolved = file.resolve()
        if resolved in visited:
            return
        try:
            raw = file.read_bytes()
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        visited.add(resolved)
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ValueError(f'SSH 配置不是有效 UTF-8：{file}') from exc
        for lineno, line in enumerate(text.splitlines(), 1):
            line = re.sub(r'^(\s*[A-Za-z]+)\s*=\s*', r'\1 ', line)
            lexer = shlex.shlex(line, posix=True)
            lexer.whitespace_split = True
            lexer.escape = ''  # Preserve Windows paths; quotes still group spaces.
            try:
                tokens = list(lexer)
            except ValueError as exc:
                raise ValueError(f'SSH 配置格式错误：{file}:{lineno}：{exc}') from exc
            if not tokens:
                continue
            keyword, values = tokens[0].lower(), tokens[1:]
            if keyword in ('host', 'include') and not values:
                raise ValueError(f'SSH 配置缺少参数：{file}:{lineno}')
            if keyword == 'host':
                for alias in values:
                    if not any(char in alias for char in '*?![]') and alias.casefold() not in seen:
                        hosts.append(alias)
                        seen.add(alias.casefold())
            elif keyword == 'include':
                for value in values:
                    pattern = _include_path(value)
                    if not pattern.is_absolute():
                        pattern = base / pattern
                    for match in sorted(glob.glob(str(pattern))):
                        visit(Path(match), depth + 1)

    visit(root, missing_ok=True)
    return hosts


def _text(value, label, required=False):
    if value is None and not required:
        return ''
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f'{label} 必须是无控制字符的文本')
    value = value.strip()
    if required and not value:
        raise ValueError(f'{label} 不能为空')
    return value


def _number(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'[0-9]+', str(value)):
        raise ValueError(f'{label} 必须是整数')
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f'{label} 必须在 {minimum}–{maximum} 之间')
    return number


def validate_connection(record):
    if not isinstance(record, dict):
        raise ValueError('连接必须是对象')
    name = _text(record.get('name'), '连接名称', True)
    host = _text(record.get('host'), '主机', True)
    if not _HOST.fullmatch(host) or host.startswith('-'):
        raise ValueError('主机含不允许的字符')
    identifier = record.get('id') or str(uuid.uuid4())
    try:
        uuid.UUID(identifier)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('连接 ID 必须为 UUID') from exc
    result = {'id': identifier, 'name': name, 'host': host, 'port': _number(record.get('port', 22), '端口', 1, 65535)}
    for key in ('user', 'identityFile', 'proxyJump'):
        value = _text(record.get(key), key)
        if value:
            if key == 'user' and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@\\-]*', value):
                raise ValueError('用户名含不允许的字符')
            if key == 'proxyJump' and (value.startswith('-') or not re.fullmatch(r'[A-Za-z0-9_\[\]][A-Za-z0-9_.:@,\[\]%-]*', value)):
                raise ValueError('跳板机含不允许的字符')
            if key == 'identityFile' and any(c in value for c in '"\'`$;|<>'):
                raise ValueError('私钥路径含不允许的字符')
            result[key] = value
    for key in _NUMBERS:
        if record.get(key) not in (None, ''):
            result[key] = _number(record[key], key, 0, 2147483647)
    if record.get('exportAlias') not in (None, ''):
        result['exportAlias'] = _text(record['exportAlias'], '导出别名', True)
    return result


def connection_args(record):
    record = validate_connection(record)
    args = ['-p', str(record['port'])]
    for key, flag in (('user', '-l'), ('identityFile', '-i'), ('proxyJump', '-J')):
        if key in record:
            args.extend([flag, record[key]])
    for key, option in _NUMBERS.items():
        if key in record:
            args.extend(['-o', f'{option}={record[key]}'])
    return args


def _validate_list(records):
    if not isinstance(records, list):
        raise ValueError('连接文件必须是数组')
    result = [validate_connection(record) for record in records]
    ids = [record['id'] for record in result]
    if len(set(ids)) != len(ids):
        raise ValueError('连接 ID 重复')
    return result


def load_connections(path):
    try:
        text = Path(path).read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return []
    try:
        return _validate_list(json.loads(text))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f'连接文件格式错误：{path}') from exc


def _atomic_bytes(path, data, check=None):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('拒绝写入符号链接')
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if check:
            check()
        if path.is_symlink():
            raise ValueError('拒绝写入符号链接')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path, data):
    _atomic_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def save_connections(path, records):
    atomic_write_json(path, _validate_list(records))


def preview_export(path, records):
    """Prepend complete Host blocks so existing trailing Host/Match cannot scope them.

    The existing bytes remain an unchanged suffix (after a single UTF-8 BOM).
    Wildcard defaults remain later, so explicit exported values take precedence.
    """
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise ValueError('拒绝写入符号链接 SSH 配置')
    existed = path.exists()
    original = path.read_bytes() if existed else b''
    records = _validate_list(records)
    if not records:
        raise ValueError('请选择至少一个连接')
    existing = {alias.casefold() for alias in discover_hosts(path)}
    newline = '\r\n' if b'\r\n' in original else '\n'
    lines = []
    for record in records:
        alias = record.get('exportAlias') or record['name']
        if not _ALIAS.fullmatch(alias):
            raise ValueError('导出别名仅允许 ASCII 字母、数字、下划线、点和连字符，且不能以连字符开头')
        if alias.casefold() in existing:
            raise ValueError(f'SSH Host 别名已存在：{alias}')
        existing.add(alias.casefold())
        lines.extend([f'Host {alias}', f'    HostName {record["host"]}', f'    Port {record["port"]}'])
        for key, keyword in (('user', 'User'), ('identityFile', 'IdentityFile'), ('proxyJump', 'ProxyJump'), *_NUMBERS.items()):
            if key in record:
                value = str(record[key])
                # Forward slashes avoid ambiguous Windows backslash quoting.
                if key == 'identityFile':
                    value = '"' + value.replace('\\', '/') + '"'
                lines.append(f'    {keyword} {value}')
        lines.append('')
    # Reset the implicit global scope before untouched leading global options.
    # Without this boundary those options would apply only to the last export.
    if original:
        lines.append('Host *')
    prefix = newline.join(lines) + newline
    bom = codecs.BOM_UTF8 if original.startswith(codecs.BOM_UTF8) else b''
    data = bom + prefix.encode('utf-8') + original[len(bom):]
    return {'path': str(path), 'text': data.decode('utf-8'), 'original': original, 'existed': existed, 'records': records}


def export_connections(preview):
    path = Path(preview['path'])

    def verify():
        if path.is_symlink():
            raise ValueError('拒绝写入符号链接 SSH 配置')
        if path.exists() != preview['existed'] or (path.read_bytes() if path.exists() else b'') != preview['original']:
            raise ValueError('SSH 配置已被其他程序修改，请重新预览')
        current = preview_export(path, preview['records'])
        if current['text'] != preview['text']:
            raise ValueError('导出预览已变化，请重新预览')

    verify()
    backup = None
    if preview['existed']:
        backup = path.with_name(f'{path.name}.backup-{uuid.uuid4().hex}')
        with backup.open('xb') as stream:
            stream.write(preview['original'])
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(backup, stat.S_IMODE(path.stat().st_mode))
    _atomic_bytes(path, preview['text'].encode('utf-8'), check=verify)
    return str(backup) if backup else None
