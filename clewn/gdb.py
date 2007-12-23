# vi:set ts=8 sts=4 sw=4 et tw=80:
#
# Copyright (C) 2007 Xavier de Gaye.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program (see the file COPYING); if not, write to the
# Free Software Foundation, Inc.,
# 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA
#
# $Id: gdb.py 204 2007-12-21 20:55:44Z xavier $

"""The Gdb application is a frontend to GDB/MI.
"""

import sys
import os
import subprocess
import re
import string
from timeit import default_timer as _timer

import gdbmi
import misc
import clewn.application as application

# gdb initial settings
GDB_INIT = """
set confirm off
set height 0
set width 0
"""
SYMBOL_COMPLETION_TIMEOUT = 20 # seconds

RE_VERSION = r'^GNU\s*gdb\s*(?P<version>[0-9.]+)\s*$'       \
             r'# RE: gdb version'
RE_COMPLETION = r'^(?P<cmd>\S+)\s*(?P<arg>\S+)(?P<rest>.*)$'\
                r'# RE: cmd 1st_arg_completion'
# ignore corrupted characters at start
# with the 'edit' command (illegal now), we get:
# 11 chars instead of 8: "103^done" (^[[m at start)
# Ox1bOx5bOx6dOx31Ox30Ox33Ox5eOx64Ox6fOx6eOx65
RE_MIRECORD = r'^.*(?P<token>\d\d\d)[^*+=](?P<result>.*)$'  \
                r'# RE: gdb/mi record'

# compile regexps
re_version = re.compile(RE_VERSION, re.VERBOSE)
re_completion = re.compile(RE_COMPLETION, re.VERBOSE)
re_mirecord = re.compile(RE_MIRECORD, re.VERBOSE)

SYMCOMPLETION = """
" print a warning message on vim command line
function s:message(msg)
    echohl WarningMsg
    echon a:msg
    let v:warningmsg = a:msg
    echohl None
endfunction

function s:prompt(msg)
    call s:message(a:msg)
    echon "\\nPress ENTER to continue."
    call getchar()
endfunction

" the symbols completion list
let s:symbols= ""

" the custom complete function
function s:Arg_break(A, L, P)
    return s:symbols
endfunction

" get the symbols completion list and define the new
" break and clear vim user defined commands
function s:symcompletion()
    call writefile([], "${ack_tmpfile}")
    let start = localtime()
    let loadmsg = "\\rLoading gdb symbols"
    call s:nbcommand("symcompletion")
    while 1
        let loadmsg = loadmsg . "."
        call s:message(loadmsg)

        " pyclewn signals that complete_tmpfile is ready for reading
        if getfsize("${ack_tmpfile}") > 0
            " ignore empty list
            if join(readfile("${ack_tmpfile}"), "") != "Ok"
                call s:prompt("\\nNo symbols found.")
                break
            endif
            let s:symbols_list = readfile("${complete_tmpfile}")
            let s:symbols= join(s:symbols_list, "\\n")
            command! -bar -nargs=* -complete=custom,s:Arg_break     \
                ${pre}break call s:nbcommand("break", <f-args>)
            command! -bar -nargs=* -complete=custom,s:Arg_break     \
                ${pre}clear call s:nbcommand("clear", <f-args>)
            call s:prompt("\\n" . len(s:symbols_list)               \
                    . " symbols fetched for break and clear completion.")
            break
        endif

        " time out has expired
        if localtime() - start > ${complete_timeout}
            call s:prompt("\\nCannot get symbols completion list.")
            break
        endif
        sleep 300m
    endwhile
endfunction

command! -bar ${pre}symcompletion call s:symcompletion()

"""

# set the logging methods
(critical, error, warning, info, debug) = misc.logmethods('gdb')

def tmpfile():
    """Return a closed file object to a new temporary file."""
    f = None
    try:
        f = misc.TmpFile('gdb')
        f.write('\n')
    finally:
        if f:
            f.close()
    return f

def gdb_batch(pgm, job):
    """Run job in gdb batch mode and return the result as a string."""
    # create the gdb script as a temporary file
    f = None
    try:
        f = misc.TmpFile('gdbscript')
        f.write(job)
    finally:
        if f:
            f.close()

    result = None
    try:
        result = subprocess.Popen((pgm, '-batch', '-nx', '-x', f.name),
                                    stdout=subprocess.PIPE).communicate()[0]
    except OSError:
        critical('cannot start gdb as "%s"', pgm)
        sys.exit()

    return result

def gdb_version(pgm):
    """Check that the gdb version is greater than 6.0."""
    version = None
    header = gdb_batch(pgm, 'show version')
    if header:
        matchobj = re_version.match(header.splitlines()[0])
        if matchobj:
            version = matchobj.group('version')

    if not version:
        critical('this is not a gdb program')
        sys.exit()
    elif version.split('.') < ['6', '0']:
        critical('invalid gdb version "%s"', version)
        sys.exit()
    else:
        info('gdb version: %s', version)


class GlobalSetup(misc.Singleton):
    """Container for gdb data constant across all Gdb instances.

    Class attributes:
        filename_complt: tuple
            list of gdb commands with file name completion
        illegal_cmds: tuple
            list of gdb illegal commands
        illegal_setargs: tuple
            list of illegal arguments to the gdb set command
        symbol_complt: tuple
            list of gdb commands with symbol completion
            they are initialized with file name completion and are set to
            symbol completion after running the Csymcompletion pyclewn command

    Instance attributes:
        gdbname: str
            gdb program name to execute
        cmds: dict
            In the commands dictionary, the key is the command name and the
            value is the command completion which can be either:
                []              no completion
                True            file name completion
                non empty list  the 1st argument completion list
        f_ack: closed file object
            temporary file used to acknowledge the end of writing to f_clist
        f_clist: closed file object
            temporary file containing the symbols completion list
        illegal_cmds_prefix: list
            List of the illegal command prefix built from illegal_cmds and the
            list of commands
        illegal_setargs_prefix: list
            List of the illegal arguments to the set command, built from
            illegal_setargs and the list of the 'set' arguments

    """

    filename_complt = (
        'cd',
        'directory',
        'file',
        'load',
        'make',
        'path',
        'restore',
        'run',
        'source',
        'start',
        'tty',
        )
    illegal_cmds = (
        '-', '+', '<', '>',
        'complete',
        'define',
        'edit',
        'end',
        'shell',
        )
    illegal_setargs = (
        'annotate',
        'confirm',
        'height',
        'width',
        )
    symbol_complt = (
        'break',
        'clear',
        )

    def init(self, gdbname):
        """Singleton initialisation."""
        self.gdbname = gdbname
        self.gdb_cmds()
        self.f_ack = tmpfile()
        self.f_clist = tmpfile()

    def __init__(self, gdbname):
        pass

    def gdb_cmds(self):
        """Get the completion lists from gdb and build the GlobalSetup lists.

        Build the following lists:
            cmds: gdb commands
            illegal_cmds_prefix
            illegal_setargs_prefix

        """
        self.cmds = {}
        dash_cmds = []  # list of gdb commands including a '-'
        firstarg_complt = ''

        # get the list of gdb commands
        for cmd in gdb_batch(self.gdbname, 'complete').splitlines():
            # sanitize gdb output: remove empty lines and trunk multiple tokens
            if not cmd:
                continue
            else:
                cmd = cmd.split()[0]

            if cmd in self.illegal_cmds     \
                    + self.filename_complt  \
                    + self.symbol_complt:
                continue
            elif '-' in cmd:
                dash_cmds.append(cmd)
            else:
                self.cmds[cmd] = []
                firstarg_complt += 'complete %s \n' % cmd

        # get first arg completion commands
        for result in gdb_batch(self.gdbname, firstarg_complt).splitlines():
            matchobj = re_completion.match(result)
            if matchobj:
                cmd = matchobj.group('cmd')
                arg = matchobj.group('arg')
                rest = matchobj.group('rest')
                if not rest:
                    self.cmds[cmd].append(arg)
                else:
                    warning('invalid completion returned by gdb: %s', result)
            else:
                error('invalid completion returned by gdb: %s', result)

        # add file name completion commands
        for cmd in self.filename_complt:
            self.cmds[cmd] = True
        for cmd in self.symbol_complt:
            self.cmds[cmd] = True

        # add commands including a '-' and that can't be made to a vim command
        self.cmds[''] = dash_cmds

        # add pyclewn commands
        for cmd in application.Application.pyclewn_cmds:
            if cmd and cmd != 'help':
                self.cmds[cmd] = ()

        self.illegal_cmds_prefix = []
        for illegal in self.illegal_cmds:
            prefix = misc.smallpref_inlist(illegal, self.cmds.keys())
            if prefix not in self.illegal_cmds_prefix:
                self.illegal_cmds_prefix.append(prefix)

        self.illegal_setargs_prefix = []
        if self.cmds.has_key('set') and self.cmds['set']:
            # remove the illegal arguments
            self.cmds['set'] = list(
                                    set(self.cmds['set'])
                                    .difference(set(self.illegal_setargs)))
            for illegal in self.illegal_setargs:
                prefix = misc.smallpref_inlist(illegal, self.cmds['set'])
                if prefix not in self.illegal_setargs_prefix:
                    self.illegal_setargs_prefix.append(prefix)

class Gdb(application.Application, misc.ProcessChannel):
    """The Gdb application is a frontend to GDB/MI.

    Instance attributes:
        globaal: GlobalSetup
            gdb global data
        results: gdbmi.Result
            storage for expected pending command results
        mi: gdbmi.Mi
            list of MiCommand instances
        cli: gdbmi.CliCommand
            the CliCommand instance
        info: gdbmi.Info
            container for the debuggee state information
        gotprmpt: boolean
            True after receiving the prompt from gdb/mi
        oob: iterator
            iterator over the list of MiCommand instances
        stream_record: list
            list of gdb/mi stream records output by a command
        lastcmd: gdbmi.Command
            the last Command instance whose result has been processed
        curcmdline: str
            the current gdb command line
        firstcmdline: None, str or ''
            the first cli command line that starts gdb
        f_init: closed file object
            temporary file containing the gdb initialisation script
        time: float
            time of the startup of the sequence of oob commands

    """

    # command line options
    opt = '-g'
    long_opt = '--gdb'
    help = 'select the gdb application (the default)'

    # list of key mappings, used to build the .pyclewn_keys.simple file
    #     key : (mapping, comment)
    mapkeys = {
        'C-Z' : ('sigint',
                    'kill the inferior running program'),
        'S-B' : ('info breakpoints',),
        'S-L' : ('info locals',),
        'S-A' : ('info args',),
        'S-S' : ('step',),
        'C-N' : ('next',),
        'S-F' : ('finish',),
        'S-R' : ('run',),
        'S-Q' : ('quit',),
        'S-C' : ('continue',),
        'S-W' : ('where',),
        'C-U' : ('up',),
        'C-D' : ('down',),
        'C-B' : ('break ${fname}:${lnum}',
                    'set breakpoint at current line'),
        'C-E' : ('clear ${fname}:${lnum}',
                    'clear breakpoint at current line'),
        'C-P' : ('print ${text}',
                    'print value of selection at mouse position'),
        'C-X' : ('print *${text}',
                    'print value referenced by word at mouse position'),
    }

    def __init__(self, nbsock, daemon, pgm, arglist):
        application.Application.__init__(self, nbsock, daemon)
        self.pgm = pgm or 'gdb'
        self.arglist = arglist
        gdb_version(self.pgm)
        self.f_init = None
        misc.ProcessChannel.__init__(self, self.getargv())

        self.globaal = GlobalSetup(self.pgm)
        self.__class__.cmds = self.globaal.cmds
        self.__class__.pyclewn_cmds = application.Application.pyclewn_cmds
        self.results = gdbmi.Result()
        self.mi = gdbmi.Mi(self)
        self.cli = gdbmi.CliCommand(self)
        self.info = gdbmi.Info()
        self.gotprmpt = False
        self.oob = None
        self.stream_record = []
        self.lastcmd = None
        self.curcmdline = ''
        self.firstcmdline = None

    def getargv(self):
        """Return the gdb argv list."""
        argv = [self.pgm]

        # use pyclewn tty as the debuggee standard input and output
        # may be overriden by --args option on pyclewn command line
        if not self.daemon and hasattr(os, 'isatty') and os.isatty(0):
            terminal = os.ttyname(0)
        elif hasattr(os, 'devnull'):
            terminal = os.devnull
        else:
            terminal = '/dev/null'
        argv += ['-tty=%s' % terminal]

        # build the gdb init temporary file
        try:
            self.f_init = misc.TmpFile('gdbscript')
            self.f_init.write(GDB_INIT)
        finally:
            if self.f_init:
                self.f_init.close()

        argv += ['-x'] + [self.f_init.name] + ['--interpreter=mi']
        if self.arglist:
            argv += self.arglist
        return argv

    def vim_script_custom(self, prefix):
        """Return gdb specific vim statements to add to the vim script.

        This is used to load the symbols completion list to the break and clear
        gdb commands.

        """
        return string.Template(SYMCOMPLETION).substitute(pre=prefix,
                                ack_tmpfile=self.globaal.f_ack.name,
                                complete_tmpfile=self.globaal.f_clist.name,
                                complete_timeout=SYMBOL_COMPLETION_TIMEOUT)

    def start(self):
        """Start gdb."""
        application.Application.start(self)
        misc.ProcessChannel.start(self)

    def prompt(self):
        if self.gotprmpt:   # print prompt only after gdb has started
            application.Application.prompt(self)

    def handle_line(self, line):
        """Process the line received from gdb."""
        if self.fileasync is None:
            return

        debug(line)
        if not line:
            error('handle_line: processing an empty line')

        # gdb/mi stream record
        elif line[0] in '~@&':
            size = len(line)
            if size > 1:
                self.stream_record.append(misc.re_escape.sub(misc.escapedchar,
                                                    line[1:size].strip('"')))
        else:
            matchobj = re_mirecord.match(line)

            # a gdb/mi result or out of band record
            if matchobj:
                token = matchobj.group('token')
                result = matchobj.group('result')
                cmd = self.results.remove(token)
                if cmd is None:
                    # ignore received duplicate token
                    pass
                else:
                    self.lastcmd = cmd
                    cmd.handle_result(result)

            # gdb/mi prompt
            elif line == '(gdb) ':
                # process all the stream records
                cmd = self.lastcmd or self.cli
                cmd.handle_strrecord(''.join(self.stream_record))
                self.stream_record = []

                # got the cli prompt
                if isinstance(self.lastcmd, gdbmi.CliCommand)   \
                                    or self.lastcmd is None:
                    # prepare the next sequence of oob commands
                    if self.lastcmd is self.cli or self.lastcmd is None:
                        self.time = _timer()
                        self.oob = self.mi()
                        if len(self.results):
                            error('all cmds have not been processed in results')
                    self.gotprmpt = True
                    self.prompt()

                # send the next oob command
                assert self.gotprmpt
                if self.oob is not None:
                    try:
                        self.oob.next().sendcmd()
                    except StopIteration:
                        self.oob = None
                        t = _timer()
                        info('oob commands execution: %f second'
                                                    % (t - self.time))
                        self.time = t
                        # send the first cli command line
                        if self.firstcmdline:
                            self.console_print("%s\n", self.firstcmdline)
                            self.cli.sendcmd(self.firstcmdline)
                        self.firstcmdline = ''
            else:
                error('handle_line: bad format of "%s"', line)

    def write(self, data):
        misc.ProcessChannel.write(self, data)
        debug(data)

    def close(self):
        """Close gdb."""
        if not self.closed:
            application.Application.close(self)

            # send an interrupt followed by the quit command
            self.sendintr()
            self.write('quit\n')
            self.console_print('\n===========\n')
            misc.ProcessChannel.close(self)

            # remove temporary files
            del self.f_init

    #-----------------------------------------------------------------------
    #   commands
    #-----------------------------------------------------------------------

    def pre_cmd(self, cmd, args):
        """The method called before each invocation of a 'cmd_xxx' method."""
        self.curcmdline = cmd
        if args:
            self.curcmdline = '%s %s' % (self.curcmdline, args)

        # echo the cmd, but not the first one and when not busy
        if self.firstcmdline is not None and cmd != 'sigint':
            if self.gotprmpt and self.oob is None:
                self.console_print('%s\n', self.curcmdline)

    def post_cmd(self, cmd, args):
        """The method called after each invocation of a 'cmd_xxx' method."""
        pass

    def default_cmd_processing(self, buf, cmd, args):
        """Process any command whose cmd_xxx method does not exist."""
        for e in self.globaal.illegal_cmds_prefix:
            if cmd.startswith(e):
                self.console_print('Illegal command in pyclewn.\n')
                self.prompt()
                return

        if cmd == 'set' and args:
            firstarg = args.split()[0]
            for e in self.globaal.illegal_setargs_prefix:
                if args and firstarg.startswith(e):
                    self.console_print('Illegal argument in pyclewn.\n')
                    self.prompt()
                    return

        if self.firstcmdline is None:
            self.firstcmdline = self.curcmdline
        else:
            self.cli.sendcmd(self.curcmdline)

    def cmd_help(self, *args):
        """Print help on gdb and on pyclewn specific commands."""
        self.console_print('Pyclewn specific commands:\n')
        application.Application.cmd_help(self, *args)
        self.console_print('\nGdb help:\n')
        self.default_cmd_processing(*args)

    def cmd_symcompletion(self, *args):
        """Populate the break and clear commands with symbols completion."""
        gdbmi.CompleteBreakCommand(self).sendcmd()

    def cmd_sigint(self, *args):
        """Send a <C-C> character to the debugger."""
        self.sendintr()
        if self.ttyname is None:
            self.console_print('\n'
                'Sorry, pyclewn is currently using pipes to talk to gdb,'
                    ' and gdb does not handle interrupts over pipes.\n'
                'As a workaround, get the pid of the program with'
                    ' the gdb command "info proc".\n'
                'And send a SIGINT signal with the shell, on vim'
                    ' command line: ":!kill -s SIGINT pid"\n'
            )
        self.prompt()

    #-----------------------------------------------------------------------
    #   netbeans events
    #-----------------------------------------------------------------------

    def balloon_text(self, text):
        """Process a netbeans balloonText event."""
        application.Application.balloon_text(self, text)
        # XXX self.show_balloon('value: "%s"' % text)

