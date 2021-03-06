# vi:set ts=8 sts=4 sw=4 et tw=80:
"""
Pyclewn miscellaneous classes and functions.
"""

# Python 2-3 compatibility.
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
from io import open

import sys
import os
import fcntl
import re
import asyncio
import tempfile
import logging
import atexit
import pprint
import itertools
import io
from collections import deque

from . import text_type, ClewnError

DOUBLEQUOTE = '"'
QUOTED_STRING = r'"((?:\\"|[^"])+)"'
NBDEBUG = 5
NBDEBUG_LEVEL_NAME = 'nbdebug'
LOG_LEVELS = ('critical', 'error', 'warning', 'info', 'debug',
                                                NBDEBUG_LEVEL_NAME)

RE_TOKEN_SPLIT = r'\s*"((?:\\"|[^"])+)"\s*|\s*([^ "]+)\s*'     \
                 r'# RE: split a string in tokens, handling quotes'
RE_ESCAPE = r'["\n\t\r\\]'                                      \
            r'# RE: escaped characters in a string'
RE_UNESCAPE = r'\\["ntr\\]'                                     \
              r'# RE: escaped characters in a quoted string'
MISSING = object()

# compile regexps
re_quoted = re.compile(QUOTED_STRING, re.VERBOSE)
re_token_split = re.compile(RE_TOKEN_SPLIT, re.VERBOSE)
re_escape = re.compile(RE_ESCAPE, re.VERBOSE)
re_unescape = re.compile(RE_UNESCAPE, re.VERBOSE)

def logmethods(name):
    """Return the set of logging methods for the 'name' logger."""
    logger = logging.getLogger(name)
    return (
        logger.critical,
        logger.error,
        logger.warning,
        logger.info,
        logger.debug,
    )

# set the logging methods
(critical, error, warning, info, debug) = logmethods('misc')

def previous_evaluation(f, previous={}):
    """Decorator for functions returning previous result when args are unchanged."""
    def _dec(*args):
        if f not in previous or previous[f][0] != args:
            previous[f] = [args, f(*args)]
        return previous[f][1]
    return _dec

def escape_char(matchobj):
    """Escape special characters in string."""
    if matchobj.group(0) == '"': return r'\"'
    if matchobj.group(0) == '\n': return r'\n'
    if matchobj.group(0) == '\t': return r'\t'
    if matchobj.group(0) == '\r': return r'\r'
    if matchobj.group(0) == '\\': return r'\\'
    assert False

def quote(msg):
    """Quote 'msg' and escape special characters."""
    return '"%s"' % re_escape.sub(escape_char, msg)

def dequote(msg):
    """Return the list of whitespace separated tokens from 'msg', handling
    double quoted substrings as a token.

    >>> print(dequote(r'"a c" b v "this \\"is\\" foobar argument" Y '))
    ['a c', 'b', 'v', 'this "is" foobar argument', 'Y']

    """
    split = msg.split(DOUBLEQUOTE)
    if len(split) % 2 != 1:
        raise ClewnError("uneven number of double quotes in '%s'" % msg)

    match = re_token_split.findall(msg)
    return [unquote(x) or y for x, y in match]

def unescape_char(matchobj):
    """Remove escape on special characters in quoted string."""
    if matchobj.group(0) == r'\"': return '"'
    if matchobj.group(0) == r'\n': return '\n'
    if matchobj.group(0) == r'\t': return '\t'
    if matchobj.group(0) == r'\r': return '\r'
    if matchobj.group(0) == r'\\': return '\\'
    assert False

def unquote(msg):
    """Remove escapes from escaped characters in a quoted string."""
    return '%s' % re_unescape.sub(unescape_char, msg)

def parse_keyval(regexp, line):
    """Return a dictionary built from a string of 'key="value"' pairs.

    The regexp format is:
        r'(key1|key2|...)=%s' % QUOTED_STRING

    """
    keyval_dict = {}
    parsed = regexp.findall(line)
    if parsed and isinstance(parsed[0], tuple) and len(parsed[0]) == 2:
        for (key, value) in parsed:
            keyval_dict[key] = unquote(value)
    else:
        debug('not an iterable of key/value pairs: "%s"', line)
    return keyval_dict

def smallest_prefix(word, other):
    """Return the smallest prefix of 'word', not prefix of 'other'."""
    assert word
    if other.startswith(word):
        return ''
    for i in range(len(word)):
        p = word[0:i+1]
        if p != other[0:i+1]:
            break
    return p

def smallpref_inlist(word, strlist):
    """Return the smallest prefix of 'word' that allows completion in 'strlist'.

    Return 'word', when it is a prefix of one of the keywords in 'strlist'.

    """
    assert strlist
    assert word not in strlist
    s = sorted(strlist + [word])
    i = s.index(word)
    previous = next = ''
    if i > 0:
        previous = smallest_prefix(word, s[i - 1]) or word
    if i < len(s) - 1:
        next = smallest_prefix(word, s[i + 1]) or word
    return max(previous, next)

def unlink(filename):
    """Unlink a file."""
    if filename and os.path.exists(filename):
        try:
            os.unlink(filename)
        except OSError:
            pass

def set_blocking(fd, blocking):
    if hasattr(os, 'set_blocking'):
        os.set_blocking(fd, blocking)
    else:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        flags = (flags & ~os.O_NONBLOCK if blocking else
                 flags | os.O_NONBLOCK)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)

def offset_gen(lines):
    """Return an iterator over the offsets of the beginning of lines.

    'lines': a list of strings
    """
    offset = 0
    for l in lines:
        yield offset
        offset += len(l)

def tmpfile(prefix):
    """Return a closed file object to a new temporary file."""
    with TmpFile(prefix) as f:
        f.write('\n')
    return f

def handle_as_lines(data, buff, handle_cb):
    """Call 'handle_cb' for each line in buff + data."""
    buff.append(data.decode())
    data = ''.join(buff)
    if '\n' in data:
        del buff[:]
        lines = data.split('\n')
        if lines[-1]:
            buff.append(lines[-1])
        for line in lines[:-1]:
            if line:
                handle_cb(line)

def cancel_after_first_completed(tasks, interrupted_cb, loop=None):
    @asyncio.coroutine
    def _cancel_after_first_completed(tasks):
        while tasks:
            done, pending = yield from(asyncio.wait(tasks,
                                return_when=asyncio.FIRST_COMPLETED,
                                loop=loop))
            for task in done:
                info(task)
                assert task in tasks
                tasks.remove(task)
            for task in pending:
                task.cancel()

    assert tasks
    if not loop:
        loop = asyncio.get_event_loop()

    main_task = asyncio.Task(_cancel_after_first_completed(tasks[:]),
                             loop=loop)
    while True:
        try:
            loop.run_until_complete(main_task)
            break
        except (KeyboardInterrupt, SystemExit):
            interrupted_cb()

    for task in tasks:
        assert task.done()
        if task.done() and not task.cancelled():
            exc = task.exception()
            if exc is not None:
                raise exc

class PrettyPrinterString(pprint.PrettyPrinter):
    """Strings are printed with str() to avoid duplicate backslashes."""

    def format(self, object, context, maxlevels, level):
        """Format un object."""
        if isinstance(object, text_type):
            return "'" + str(object) + "'", True, False
        return pprint._safe_repr(object, context, maxlevels, level)

def pformat(object, indent=1, width=80, depth=None):
    """Format a Python object into a pretty-printed representation."""
    return PrettyPrinterString(
                    indent=indent, width=width, depth=depth).pformat(object)

class TmpFile(object):
    """A container for a temporary writtable file object.

    Support the context management protocol.

    """
    def __init__(self, prefix):
        self.f = None
        self.name = None
        try:
            fd, self.name = tempfile.mkstemp('.clewn', prefix)
            os.close(fd)
            self.f = open(self.name, 'w')
        except (OSError, IOError):
            unlink(self.name)
            critical('cannot create temporary file'); raise
        else:
            atexit.register(unlink, self.name)

    def write(self, data):
        self.f.write(data)

    def close(self):
        if self.f:
            self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        self.close()

    def __del__(self):
        unlink(self.name)

class Singleton(object):
    """A singleton, there is only one instance of this class."""

    def __new__(cls, *args, **kwds):
        """Create the single instance."""
        it = cls.__dict__.get("__it__")
        if it is not None:
            return it
        cls.__it__ = it = object.__new__(cls)
        it.init(*args, **kwds)
        return it

    def init(self, *args, **kwds):
        """Override in subclass."""
        pass

class StderrHandler(logging.StreamHandler):
    """Stderr logging handler."""

    def __init__(self):
        self.strbuf = io.StringIO()
        self.doflush = True
        logging.StreamHandler.__init__(self, self.strbuf)

    def should_flush(self, doflush):
        """Set flush mode."""
        self.doflush = doflush

    def write(self, string):
        """Write to the StringIO buffer."""
        self.strbuf.write(string)

    def flush(self):
        """Flush to stderr when enabled."""
        if self.doflush:
            value = self.strbuf.getvalue()
            if value:
                print(value, end='', file=sys.stderr)
                self.strbuf.truncate(0)

    def close(self):
        """Close the handler."""
        self.flush()
        self.strbuf.close()
        logging.StreamHandler.close(self)

def index_list(txt, sub, start, end):
    """Return the list of indexes of 'sub' in 'txt'."""
    l = len(txt)
    indexes = deque()
    while start != -1 and start < l:
        start = txt.find(sub, start, end)
        if start >= 0:
            indexes.append(start)
            start += 1
    return indexes

def match_closing(txt, matches, start=0, end=None):
    """Match the first opening matches[0] with a closing matches[1].

    >>> match_closing('bar{foo}', ('{', '}'))
    (3, 7)
    >>> match_closing('{foo{bar{}}}', ('{', '}'))
    (0, 11)
    >>> match_closing('{foo{bar', ('{', '}'))
    Traceback (most recent call last):
    ...
    clewn.ClewnError: error: one of the substring is missing
    >>> match_closing('{foo{bar}', ('{', '}'))
    Traceback (most recent call last):
    ...
    clewn.ClewnError: error: '{' at 0 not matched with any '}'

    """
    opensub = index_list(txt, matches[0], start, end)
    closesub = index_list(txt, matches[1], start, end)
    if not opensub or not closesub:
        raise ClewnError('error: one of the substring is missing')

    start = opensub.popleft()
    c = None
    stack = deque()
    while closesub:
        if c is None:
            c = closesub.popleft()
        o = opensub.popleft() if opensub else None
        if o and o < c:
            stack.append(o)
            continue

        while stack:
            stack.pop()
            if not closesub:
                break
            c = closesub.popleft()
            if o and o < c:
                stack.append(o)
                break
        else:
            return start, c
    else:
        raise ClewnError("error: '%s' at %d not matched with any '%s'"
                         % (matches[0], start, matches[1]))

def split_matches(txt, matches, start=0):
    """Split 'txt' into matching matches[0] with matches[1] at the same level.

    >>> txt = r'threads=[{id="1",frame={level="0",args=[{name="t",value="0x7f"},{name="s",value="0x40\"[{:0>2}:{:0>2}:{:0>2}.{:0>6}]\""}],line="5"},core="3"}],current-thread-id="1"'

    >>> split_matches(txt, ('{', '}'))
    ['{id="1",frame={level="0",args=[{name="t",value="0x7f"},{name="s",value="0x40"[{:0>2}:{:0>2}:{:0>2}.{:0>6}]""}],line="5"},core="3"}']

    >>> split_matches(txt, ('{', '}'), 17)
    ['{level="0",args=[{name="t",value="0x7f"},{name="s",value="0x40"[{:0>2}:{:0>2}:{:0>2}.{:0>6}]""}],line="5"}']

    >>> split_matches(txt, ('{', '}'), 74)
    ['{:0>2}', '{:0>2}', '{:0>2}', '{:0>6}']

    """
    matchlist = []
    while True:
        try:
            start, end = match_closing(txt, matches, start)
        except ClewnError:
            break
        matchlist.append(txt[start:end+1])
        start = end + 1
    return matchlist

def _test():
    """Run the doctests."""
    import doctest
    doctest.testmod()

if __name__ == "__main__":
    _test()

