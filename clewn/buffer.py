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
"""The Vim buffers module."""

import os.path
import re

import clewn.misc as misc

FRAME_ANNO_ID = 'frame'

RE_CLEWNAME = r'^\s*(?P<path>.*)\(clewn\)_\w+$'     \
              r'# RE: a valid ClewnBuffer name'

# compile regexps
re_clewname = re.compile(RE_CLEWNAME, re.VERBOSE)

# set the logging methods
(critical, error, warning, info, debug) = misc.logmethods('buf')
Unused = critical
Unused = warning
Unused = info
Unused = debug

def is_clewnbuf(bufname):
    """Return True if bufname is the name of a clewn buffer."""
    matchobj = re_clewname.match(bufname)
    if matchobj:
        path = matchobj.group('path')
        if not path or os.path.exists(path):
            return True
    return False

class Buffer(dict):
    """A Vim buffer is a dictionary of annotations {anno_id: annotation}.

    Instance attributes:
        name: readonly property
            full pathname
        buf_id: int
            netbeans buffer number, starting at one
        nbsock: netbeans.Netbeans
            the netbeans asynchat socket
        registered: boolean
            True: buffer registered to Vim with netbeans
        editport: ClewnBuffer
            the ClewnBuffer associated with this Buffer instance
        lnum: int
            cursor line number
        col: int
            cursor column
        type_num: int
            last sequence number of a defined annotation
        bp_tnum: int
            sequence number of the enabled breakpoint annotation
            the sequence number of the disabled breakpoint annotation
            is bp_tnum + 1
        frame_tnum: int
            sequence number of the frame annotation

    """

    def __init__(self, name, buf_id, nbsock):
        """Constructor."""
        self.__name = name
        self.buf_id = buf_id
        self.nbsock = nbsock
        self.registered = False
        self.editport = None
        self.lnum = None
        self.col = None
        self.type_num = self.bp_tnum = self.frame_tnum = 0

    def define_frameanno(self):
        """Define the frame annotation."""
        if not self.frame_tnum:
            self.type_num += 1
            self.frame_tnum = self.type_num
            self.nbsock.send_cmd(self, 'defineAnnoType',
                '%d "frame" "" "=>" none %d' % (self.frame_tnum, 0xefb735))
        return self.frame_tnum

    def define_bpanno(self):
        """Define the two annotations for breakpoints."""
        if not self.bp_tnum:
            self.bp_tnum = self.type_num + 1
            self.type_num += 2 # two annotations are defined in sequence
            self.nbsock.send_cmd(self, 'defineAnnoType',
                '%d "bpEnabled" "" "bp" none %d' % (self.bp_tnum, 0x0c3def))
            self.nbsock.send_cmd(self, "defineAnnoType",
                '%d "bpDisabled" "" "bp" none %d' % (self.type_num , 0x3fef4b))
        return self.bp_tnum

    def add_anno(self, anno_id, lnum):
        """Add an annotation."""
        assert not anno_id in self.keys()
        if anno_id == FRAME_ANNO_ID:
            self[anno_id] = FrameAnnotation(self, lnum, self.nbsock)
        else:
            self[anno_id] = Annotation(self, lnum, self.nbsock)
        self.update(anno_id)

    def delete_anno(self, anno_id):
        """Delete an annotation."""
        assert anno_id in self.keys()
        self[anno_id].remove_anno()
        del self[anno_id]

    def update(self, anno_id=None, disabled=False):
        """Update the buffer with netbeans."""
        # open file in netbeans
        if not self.registered:
            self.nbsock.send_cmd(self, 'editFile', misc.quote(self.name))
            self.nbsock.send_cmd(self, 'putBufferNumber', misc.quote(self.name))
            self.nbsock.send_cmd(self, 'stopDocumentListen')
            self.registered = True

        # update annotations
        if anno_id:
            self[anno_id].update(disabled)
        else:
            for anno_id in self.keys():
                self[anno_id].update()

    def removeall(self, lnum=None):
        """Remove all netbeans annotations at line lnum.

        When lnum is None, remove all annotations.

        """
        for anno_id in self.keys():
            if lnum is None or self[anno_id].lnum == lnum:
                self[anno_id].remove_anno()

    # readonly property
    def getname(self):
        """Buffer full path name."""
        return self.__name
    name = property(getname, None, None, getname.__doc__)

class Annotation(object):
    """A netbeans annotation.

    Instance attributes:
        buf: Buffer
            buffer container
        lnum: int
            line number
        nbsock: netbeans.Netbeans
            the netbeans asynchat socket
        disabled: boolean
            True when the breakpoint is disabled
        sernum: int
            serial number of this placed annotation,
            used to be able to remove it
        is_set: boolean
            True when annotation has been added with netbeans

    """

    def __init__(self, buf, lnum, nbsock, disabled=False):
        """Constructor."""
        self.buf = buf
        self.lnum = lnum
        self.nbsock = nbsock
        self.disabled = disabled
        self.sernum = nbsock.last_sernum
        self.is_set = False

    def update(self, disabled=False):
        """Update the annotation."""
        if self.disabled != disabled:
            self.remove_anno()
            self.disabled = disabled
        if not self.is_set:
            typeNum = self.buf.define_bpanno()
            if self.disabled:
                typeNum += 1
            self.nbsock.send_cmd(self.buf, 'addAnno', '%d %d %d/0 -1'
                                % (self.sernum, typeNum, self.lnum))
            self.nbsock.last_buf = self.buf
            self.nbsock.last_buf.lnum = self.lnum
            self.nbsock.last_buf.col = 0

            self.nbsock.send_cmd(self.buf, 'setDot', '%d/0' % self.lnum)
            self.is_set = True

    def remove_anno(self):
        """Remove the annotation."""
        if self.buf.registered and self.is_set:
            self.nbsock.send_cmd(self.buf, 'removeAnno', str(self.sernum))
        self.is_set = False

    def __repr__(self):
        """Return breakpoint information."""
        state = 'enabled'
        if self.disabled:
            state = 'disabled'
        return 'bp %s at line %d' % (state, self.lnum)

class FrameAnnotation(misc.Singleton, Annotation):
    """The frame annotation is the sign set in the current frame."""

    def init(self, buf, lnum, nbsock):
        """Singleton initialisation."""
        unused = buf
        unused = lnum
        self.disabled = False
        self.sernum = nbsock.last_sernum
        self.nbsock = nbsock

    def __init__(self, buf, lnum, nbsock):
        """Constructor."""
        self.buf = buf
        self.lnum = lnum
        unused = nbsock
        self.disabled = False
        self.is_set = False
        # this happens when running regtests
        if self.nbsock is not nbsock:
            self.nbsock = nbsock
            self.sernum = nbsock.last_sernum

    def update(self, disabled=False):
        """Update the annotation."""
        unused = disabled
        if not self.is_set:
            typeNum = self.buf.define_frameanno()
            self.nbsock.send_cmd(self.buf, 'addAnno', '%d %d %d/0 -1'
                                % (self.sernum, typeNum, self.lnum))
            self.nbsock.last_buf = self.buf
            self.nbsock.last_buf.lnum = self.lnum
            self.nbsock.last_buf.col = 0

            self.nbsock.send_cmd(self.buf, 'setDot', '%d/0' % self.lnum)
            self.is_set = True

    def __repr__(self):
        """Return frame information."""
        return 'frame at line %d' % self.lnum

class BufferSet(dict):
    """The Vim buffer set is a dictionary of {pathname: Buffer instance}.

    Instance attributes:
        nbsock: netbeans.Netbeans
            the netbeans asynchat socket
        buf_list: python list
            the list of Buffer instances indexed by netbeans 'bufID'
        anno_dict: dictionary
            global dictionary of all annotations {anno_id: Buffer instance}

    A Buffer instance is never removed from BufferSet.

    """

    def __init__(self, nbsock):
        """Constructor."""
        self.nbsock = nbsock
        self.buf_list = []
        self.anno_dict = {}

    def add_anno(self, anno_id, pathname, lnum):
        """Add the annotation to the global list and to the buffer annotation
        list."""
        if not isinstance(lnum, int) or lnum <= 0:
            raise ValueError('"lnum" must be strictly positive: %s' % lnum)
        if anno_id in self.anno_dict.keys():
            raise KeyError('"anno_id" already exists:  %s' % anno_id)
        if not os.path.isabs(pathname):
            raise ValueError(
                '"pathname" is not an absolute path: %s' % pathname)
        buf = self[pathname]
        self.anno_dict[anno_id] = buf
        buf.add_anno(anno_id, lnum)

    def update_anno(self, anno_id, disabled=False):
        """Update the annotation."""
        if anno_id not in self.anno_dict.keys():
            raise KeyError('"anno_id" does not exist:  %s' % anno_id)
        self.anno_dict[anno_id].update(anno_id, disabled)

    def delete_anno(self, anno_id):
        """Delete the annotation from the global list and from the buffer
        annotation list.

        """
        if anno_id not in self.anno_dict.keys():
            raise KeyError('"anno_id" does not exist:  %s' % anno_id)
        self.anno_dict[anno_id].delete_anno(anno_id)
        del self.anno_dict[anno_id]

    def show_frame(self, pathname=None, lnum=1):
        """Show the frame annotation.

        The frame annotation is unique.
        Remove the frame annotation when pathname is None.

        """
        if not isinstance(lnum, int) or lnum <= 0:
            raise ValueError('"lnum" must be strictly positive: %s' % lnum)
        if FRAME_ANNO_ID in self.anno_dict.keys():
            self.delete_anno(FRAME_ANNO_ID)
        if pathname:
            self.add_anno(FRAME_ANNO_ID, pathname, lnum)

    def add_bp(self, bp_id, pathname, lnum):
        """Add the breakpoint to the global list and to the buffer annotation list."""
        if not isinstance(lnum, int) or lnum <= 0:
            raise ValueError('"lnum" must be strictly positive: %s' % lnum)
        if not bp_id in self.anno_dict.keys():
            self.add_anno(bp_id, pathname, lnum)
        else:
            error('attempt to add a breakpoint that already exists')

    def update_bp(self, bp_id, disabled=False):
        """Update the breakpoint.

        Return True when successful.

        """
        if bp_id in self.anno_dict.keys():
            self.update_anno(bp_id, disabled)
            return True
        else:
            error('attempt to update an unknown annotation')
            return False

    def getbuf(self, buf_id):
        """Return the Buffer at idx in list."""
        assert isinstance(buf_id, int)
        if buf_id <= 0 or buf_id > len(self.buf_list):
            return None
        return self.buf_list[buf_id - 1]

    def delete_all(self, pathname=None, lnum=None):
        """Delete all annotations.

        Delete all annotations in pathname at lnum.
        Delete all annotations in pathname if lnum is None.
        Delete all annotations in all buffers if pathname is None.
        The anno_dict dictionary is updated accordingly.
        Return the list of deleted anno_id.

        """
        if pathname is None:
            lnum = None
        elif not os.path.isabs(pathname):
            raise ValueError(
                '"pathname" is not an absolute path: %s' % pathname)

        deleted = []
        for buf in self.buf_list:
            if pathname is None or buf.name == pathname:
                # remove annotations from the buffer
                buf.removeall(lnum)

                # delete annotations from anno_dict
                anno_list = []
                for (anno_id, anno) in buf.iteritems():
                    if lnum is None or anno.lnum == lnum:
                        del self.anno_dict[anno_id]
                        anno_list.append(anno_id)

                # delete annotations from the buffer
                for anno_id in anno_list:
                    del buf[anno_id]

                deleted.extend(anno_list)

        return deleted

    def get_lnum_list(self, pathname):
        """Return the list of line numbers of all enabled breakpoints.

        A line number may be duplicated in the list.

        """
        lnum_list = []
        if pathname in self:
            lnum_list = [anno.lnum for anno in self[pathname].values()
                        if not anno.disabled
                        and not isinstance(anno, FrameAnnotation)]
        return lnum_list

    #-----------------------------------------------------------------------
    #   Dictionary methods
    #-----------------------------------------------------------------------
    def __getitem__(self, pathname):
        """Get Buffer with pathname as key, instantiate one when not found.

        The pathname parameter must be an absolute path name.

        """
        if not isinstance(pathname, str)          \
                or (not os.path.isabs(pathname)   \
                    and not is_clewnbuf(pathname)):
            raise ValueError(
                '"pathname" is not an absolute path: %s' % pathname)
        if not pathname in self:
            # netbeans buffer numbers start at one
            buf = Buffer(pathname, len(self.buf_list) + 1, self.nbsock)
            self.buf_list.append(buf)
            dict.__setitem__(self, pathname, buf)
        return dict.__getitem__(self, pathname)

    def __setitem__(self, pathname, item):
        """Mapped to __getitem__."""
        unused = item
        self.__getitem__(pathname)

    def setdefault(self, pathname, failobj=None):
        """Mapped to __getitem__."""
        unused = failobj
        return self.__getitem__(pathname)

    def __delitem__(self, key):
        """A key is never removed."""
        pass

    def popitem(self):
        """A key is never removed."""
        pass

    def pop(self, key, *args):
        """A key is never removed."""
        pass

    def update(self, dict=None, **kwargs):
        """Not implemented."""
        unused = self
        unused = dict
        unused = kwargs
        assert False, 'not implemented'

    def copy(self):
        """Not implemented."""
        unused = self
        assert False, 'not implemented'
