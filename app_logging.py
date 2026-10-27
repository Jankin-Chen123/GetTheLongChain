"""Bounded JSON logs with per-request correlation and credential redaction."""

import json
import logging
import re
import traceback
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import unquote, urlsplit

REQUEST_CONTEXT = ContextVar('request_log_context', default=None)
LOG_DIR = Path(__file__).resolve().parent / 'logs'


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(record.event_data, ensure_ascii=False, separators=(',', ':'))


def configure_logs(directory=LOG_DIR, max_bytes=5 * 1024 * 1024, backups=5):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('getthelongchain')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    for filename, level in [('runtime.log', logging.INFO), ('error.log', logging.ERROR)]:
        handler = RotatingFileHandler(directory / filename, maxBytes=max_bytes, backupCount=backups,
                                      encoding='utf-8')
        handler.setLevel(level)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


LOGGER = configure_logs()


def remember_secrets(*values):
    context = REQUEST_CONTEXT.get()
    if context is not None:
        context['secrets'].extend(value for value in values if isinstance(value, str) and value)


def remember_proxy(value):
    remember_secrets(value)
    try:
        parsed = urlsplit(value)
        remember_secrets(unquote(parsed.username or ''), unquote(parsed.password or ''), parsed.hostname)
    except ValueError:
        pass


def redact(value):
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(item) for item in value[:30]]
    if not isinstance(value, str):
        return value
    context = REQUEST_CONTEXT.get()
    for secret in sorted(set(context['secrets'] if context else []), key=len, reverse=True):
        value = value.replace(secret, '[REDACTED]')
    value = re.sub(r'(?i)Bearer\s+\S+', '[REDACTED]', value)
    value = re.sub(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED]', value)
    value = re.sub(r'(?i)(?:https?|socks\w*)://\S+', '[URL REDACTED]', value)
    value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL REDACTED]', value)
    value = re.sub(r'\b[A-Za-z0-9_-]{30,}\b', '[REDACTED]', value)
    return ''.join(c for c in value if c.isprintable() or c in '\n\t')[:2000]


def event(name, *, level=logging.INFO, **fields):
    context = REQUEST_CONTEXT.get() or {}
    payload = {'time': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='milliseconds'),
               'level': logging.getLevelName(level), 'event': name,
               'request_id': context.get('request_id'), **redact(fields)}
    LOGGER.log(level, '', extra={'event_data': payload})


def set_stage(name):
    context = REQUEST_CONTEXT.get()
    if context is not None:
        context['stage'] = name
    event('stage', stage=name)


def safe_traceback(exc):
    # No exception text, source lines, or local variables: those may hold secrets.
    return [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)[-16:]]
