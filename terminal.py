# Copyright 2013-2016 Evgeny Golyshev <eugulixes@gmail.com>
# Copyright 2016 Dmitriy Shilin <sdadeveloper@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import array
import html
import re

import yaml


MAGIC_NUMBER = 0x07000000


class Terminal:
    def __init__(self, rows=24, cols=80):
        self._cols = cols
        self._rows = rows
        self._cur_y = None
        self._cur_x = None

        # The following two fields are used only for implementation of
        # storing (sc) and restoring (rc) the current cursor position.
        self._cur_x_bak = 0
        self._cur_y_bak = 0

        self._screen = None

        # eol stands for 'end of line' and is set to True when the cursor
        # reaches the right side of the screen.
        self._eol = False
        self._top = None
        self._bottom = None
        self._right = None

        self._sgr = None  # Select Graphic Rendition

        self._buf = ''
        self._outbuf = ''

        with open('linux_console.yml') as f:
            sequences = yaml.load(f.read())

        self.control_characters = sequences['control_characters']

        self.esc_re = []
        self.new_sci_seq = {}
        for k, v in sequences['escape_sequences'].items():
            self.new_sci_seq[k.replace('\\E', '\x1b')] = v

        self.new_sci_seq_re = {}
        for k, v in sequences['escape_sequences_re'].items():
            self.new_sci_seq_re[k.replace('\\E', '\x1b')] = v

        self.new_sci_seq_re_compiled = []
        self.csi_seq = {
            '`': (self.cap_kb2, [1]),
        }
        self.init()
        self.cap_rs1()
        # self._top = None
        # self._bottom = None

    def cap_civis(self):
        pass

    def cap_cvvis(self):
        pass

    def init(self):
        for k, v in list(self.new_sci_seq_re.items()):
            res = k.replace('[', '\[').\
                    replace('%d', '([0-9]+)')
            self.new_sci_seq_re_compiled.append(
                (re.compile(res), v)
            )

        d = {
            r'\[\??([0-9;]*)([@ABCDEFGHJKLMPXacdefghlmnqrstu`])':
                self.cap_ignore,
            r'\]([^\x07]+)\x07': self.cap_ignore,
        }

        for k, v in list(d.items()):
            self.esc_re.append((re.compile('\x1b' + k), v))

    def cap_rs1(self, s=''):
        """Reset terminal completely to sane modes."""
        cells_number = self._cols * self._rows
        self._screen = array.array('L', [MAGIC_NUMBER] * cells_number)
        self._sgr = MAGIC_NUMBER
        self._cur_x_bak = self._cur_x = 0
        self._cur_y_bak = self._cur_y = 0
        self._eol = False
        self._top = 0
        self._bottom = self._rows - 1
        self._right = self._cols - 1

        self._buf = ''
        self._outbuf = ''

    def peek(self, left_border, right_border, inclusively=False):
        """Captures and returns a rectangular region of the screen.

        The ``left_border`` and ``right_border`` arguments must be tuples or
        lists of coordinates ``(x1, y1)`` and ``(x2, y2)``, respectively.

        The name of the method was inherited from AjaxTerm, developers of
        which, in turn, inherited it from BASIC. See poke.
        """
        x1, y1 = left_border
        x2, y2 = right_border
        begin = self._cols * y1 + x1
        end = self._cols * y2 + x2 + (1 if inclusively else 0)
        return self._screen[begin:end]

    def poke(self, pos, s):
        """Puts the specified string ``s`` on the screen staring at the
        specified position ``pos``.

        The ``pos`` argument must be a tuple or list of coordinates ``(x, y)``.

        The name of the method was inherited from AjaxTerm, developers of
        which, in turn, inherited it from BASIC. See peek.
        """
        x, y = pos
        begin = self._cols * y + x
        self._screen[begin:begin + len(s)] = s

    def zero(self, left_border, right_border, inclusively=False):
        """Clears the area from ``left_border`` to ``right_border``.

        The ``left_border`` and ``right_border`` arguments must be tuples or
        lists of coordinates ``(x1, y1)`` and ``(x2, y2)``, respectively.
        """
        x1, y1 = left_border
        x2, y2 = right_border
        begin = self._cols * y1 + x1
        end = self._cols * y2 + x2 + (1 if inclusively else 0)
        length = end - begin  # the length of the area which have to be cleared
        self._screen[begin:end] = array.array('L', [MAGIC_NUMBER] * length)
        return length

    def scroll_up(self, y1, y2):
        """Moves the area specified by coordinates 0, ``y1`` and 0, ``y2`` up 1
        row.
        """
        # Start copying from the next row (y1 + 1).
        line = self.peek((0, y1 + 1), (self._cols, y2))
        self.poke((0, y1), line)
        self.zero((0, y2), (self._cols, y2))

    def scroll_down(self, y1, y2):
        """Moves the area specified by coordinates 0, ``y1`` and 0, ``y2`` down
        1 row.
        """
        line = self.peek((0, y1), (self._cols, y2 - 1))
        self.poke((0, y1 + 1), line)
        self.zero((0, y1), (self._cols, y1))

    def scroll_right(self, x, y):
        """Moves a piece of a row specified by coordinates ``x`` and ``y``
        right by 1 position."""

        self.poke((x + 1, y), self.peek((x, y), (self._cols, y)))
        self.zero((x, y), (x, y), inclusively=True)

    def cursor_down(self):
        """Moves the cursor down by 1 position. If the cursor reaches the
        bottom of the screen, its content moves up 1 row. """
        if self._top <= self._cur_y <= self._bottom:
            self._eol = False
            q, r = divmod(self._cur_y + 1, self._bottom + 1)
            if q:
                self.scroll_up(self._top, self._bottom)
                self._cur_y = self._bottom
            else:
                self._cur_y = r

    def cursor_right(self):
        """Moves the cursor right by 1 position."""
        q, r = divmod(self._cur_x + 1, self._cols)
        if q:
            self._eol = True
        else:
            self._cur_x = r

    def echo(self, c):
        """Puts the specified character ``c`` on the screen and moves the
        cursor right by 1 position. If the cursor reaches the right side of the
        screen, it is moved to the next line."""
        if self._eol:
            self.cursor_down()
            self._cur_x = 0

        pos = self._cur_y * self._cols + self._cur_x
        self._screen[pos] = self._sgr | ord(c)
        self.cursor_right()

    # def csi_E(self, l):
    #     self.csi_B(l)
    #     self._cur_x = 0
    #     self._eol = False

    # def csi_F(self, l):
    #     self.csi_A(l)
    #     self._cur_x = 0
    #     self._eol = False

    # def csi_a(self, l):
    #     self.csi_C(l)

    # def csi_c(self, l):
    #     # '\x1b[?0c' 0-8 cursor size
    #     pass

    # def csi_e(self, l):
    #     self.csi_B(l)

    # def csi_f(self, l):
    #     self.csi_H(l)

    # новый стиль именования методов, реализующих возможности

    def cap_cub1(self):
        """Moves the cursor left by 1 position. """
        self._cur_x = max(0, self._cur_x - 1)

    def cap_ht(self):
        x = self._cur_x + 8
        q, r = divmod(x, 8)
        self._cur_x = (q * 8) % self._cols

    def cap_ind(self):
        """Scrolls the screen up moving its content down. """
        self.cursor_down()

    def cap_cr(self):
        """Does carriage return. """
        self._eol = False
        self._cur_x = 0

    # TODO: rework later
    def esc_da(self):
        self._outbuf = "\x1b[?6c"  # u8

    # XXX: never used
    def esc_ri(self, s):
        self._cur_y = max(self._top, self._cur_y - 1)
        if self._cur_y == self._top:
            self.scroll_down(self._top, self._bottom)

    # XXX: never used
    def csi_at(self, l):
        for i in range(l[0]):
            self.cap_ich1()

    def cap_ignore(self, *s):
        pass

    def cap_set_colour_pair(self, mo=None, p1=None, p2=None):
        if mo:
            p1 = int(mo.group(1))
            p2 = int(mo.group(2))

        if p1 == 0 and p2 == 10:  # sgr0
            self._sgr = MAGIC_NUMBER
        elif p1 == 39 and p2 == 49:  # op
            self._sgr = MAGIC_NUMBER
        else:
            self.cap_set_colour(colour=p1)
            self.cap_set_colour(colour=p2)

    def cap_set_colour(self, mo=None, colour=None):
        if mo:
            colour = int(mo.group(1))

        if colour == 0:
            self._sgr = MAGIC_NUMBER
        elif colour == 1:  # bold
            self._sgr = (self._sgr | 0x08000000)
        elif colour == 2:  # dim
            pass
        elif colour == 4:  # smul
            pass
        elif colour == 5:  # blink
            pass
        elif colour == 7:  # smso or rev
            self._sgr = 0x70000000
        elif colour == 10:  # rmpch
            pass
        elif colour == 11:  # smpch
            pass
        elif colour == 24:  # rmul
            pass
        elif colour == 27:  # rmso
            self._sgr = MAGIC_NUMBER
        elif 30 <= colour <= 37:  # setaf
            c = colour - 30
            self._sgr = (self._sgr & 0xf8ffffff) | (c << 24)
        elif colour == 39:
            self._sgr = MAGIC_NUMBER
        elif 40 <= colour <= 47:  # setab
            c = colour - 40
            self._sgr = (self._sgr & 0x0fffffff) | (c << 28)
        elif colour == 49:
            self._sgr = MAGIC_NUMBER

    def cap_sgr0(self, mo=None, p1=''):
        self.cap_set_colour_pair(p1=0, p2=10)

    def cap_op(self, mo=None, p1=''):
        self.cap_set_colour_pair(p1=39, p2=49)

    def cap_noname(self, p1=''):
        self.cap_set_colour(colour=0)

    def cap_bold(self, p1=''):
        self.cap_set_colour(colour=1)

    def cap_dim(self, p1=''):
        self.cap_set_colour(colour=2)

    def cap_smul(self, p1=''):
        self.cap_set_colour(colour=4)

    def cap_blink(self, p1=''):
        self.cap_set_colour(colour=5)

    def cap_smso_rev(self, p1=''):
        self.cap_set_colour(colour=7)

    def cap_rmpch(self, p1=''):
        self.cap_set_colour(colour=10)

    def cap_smpch(self, p1=''):
        self.cap_set_colour(colour=11)

    def cap_rmul(self, p1=''):
        self.cap_set_colour(colour=24)

    def cap_rmso(self, p1=''):
        self.cap_set_colour(colour=27)

    def cap_sc(self, s=''):
        """Save cursor position """
        self._cur_x_bak = self._cur_x
        self._cur_y_bak = self._cur_y

    def cap_rc(self, s=''):
        """Restore cursor to position of last sc """
        self._cur_x = self._cur_x_bak
        self._cur_y = self._cur_y_bak
        self._eol = False

    def cap_ich1(self, l=[1]):
        """Insert character """
        self.scroll_right(self._cur_x, self._cur_y)

    def cap_smir(self, l=''):
        """Insert mode (enter) """
        pass

    def cap_rmir(self, l=''):
        """End insert mode """
        pass

    def cap_smso(self, l=''):
        """Begin standout mode """
        self._sgr = 0x70000000

    def cap_kcuu1(self, l=[1]):
        """sent by terminal up-arrow key """
        self._cur_y = max(self._top, self._cur_y - l[0])

    def cap_kcud1(self, l=[1]):
        """sent by terminal down-arrow key """
        self._cur_y = min(self._bottom, self._cur_y + l[0])

    def cap_kcuf1(self, l=[1]):
        """sent by terminal right-arrow key """
        self.cap_cuf(p1=0)

    def cap_cuf(self, mo=None, p1=None):
        if mo:
            p1 = int(mo.group(1))

        self._cur_x = min(self._right, self._cur_x + p1)
        self._eol = False

    def cap_kcub1(self, l=[1]):
        """sent by terminal left-arrow key """
        self._cur_x = max(0, self._cur_x - l[0])
        self._eol = False

    def cap_kb2(self, l=[1]):
        """center of keypad """
        self._cur_x = min(self._cols, l[0]) - 1

    def cap_home(self, l=[1, 1]):
        """Home cursor """
        self._cur_x = min(self._cols, l[1]) - 1
        self._cur_y = min(self._rows, l[0]) - 1
        self._eol = False

    def cap_ed(self, l=None):
        """Clears the screen from the current cursor position to the end of the
        screen.
        """
        self.zero((self._cur_x, self._cur_y), (self._cols, self._rows - 1))

    def cap_el(self, l=[0]):
        """Clear to end of line """
        if l[0] == 0:
            self.zero((self._cur_x, self._cur_y), (self._cols, self._cur_y))
        elif l[0] == 1:
            self.zero((0, self._cur_y), (self._cur_x, self._cur_y),
                      inclusively=True)
        elif l[0] == 2:
            self.zero((0, self._cur_y), (self._cols, self._cur_y))

    def cap_el1(self, l=[0]):
        self.cap_el([1])

    def cap_il1(self, l=''):
        """Add new blank line """
        self.cap_il(p1=1)

    def cap_dl1(self, l=''):
        """Delete line """
        self.cap_dl(p1=1)

    def cap_dch1(self, l=''):
        """Delete character """
        self.cap_dch(1)

    def cap_vpa(self, mo):
        """Set vertical position to absolute #1 """
        p = int(mo.group(1))
        self._cur_y = min(self._rows, p) - 1

    def cap_il(self, mo=None, p1=None):
        """Add #1 new blank lines """
        if mo:
            tmp = mo.group(1)
            p1 = int(mo.group(1))

        for i in range(p1):
            if self._cur_y < self._bottom:
                self.scroll_down(self._cur_y, self._bottom)

    def cap_dl(self, mo=None, p1=None):
        """Delete #1 lines """
        if mo:
            p1 = int(mo.group(1))

        if self._top <= self._cur_y <= self._bottom:
            for i in range(p1):
                self.scroll_up(self._cur_y, self._bottom)

    def cap_dch(self, mo=None, p1=None):
        """Delete #1 chars """
        if mo:
            p1 = int(mo.group(1))

        w, cx, cy = self._cols, self._cur_x, self._cur_y
        end = self.peek((cx, cy), (w, cy))
        self.cap_el([0])
        self.poke((cx, cy), end[p1:])

    def cap_csr(self, mo):
        """Change to lines #1 through #2 (VT100) """
        p1 = int(mo.group(1))
        p2 = int(mo.group(2))
        self._top = min(self._rows - 1, p1 - 1)
        self._bottom = min(self._rows - 1, p2 - 1)
        self._bottom = max(self._top, self._bottom)

    def cap_ech(self, mo):
        """Erase #1 characters """
        p = int(mo.group(1))
        self.zero((self._cur_x, self._cur_y), (self._cur_x + p, self._cur_y),
                  inclusively=True)

    def cap_cup(self, mo):
        """Move to row #1 col #2 """
        p1 = int(mo.group(1))
        p2 = int(mo.group(2))
        self._cur_x = min(self._cols, p2) - 1
        self._cur_y = min(self._rows, p1) - 1
        self._eol = False

    def exec_escape_sequence(self):
        e = self._buf

        if e == '\x1b[?2004l':
            pass

        method_name = self.new_sci_seq.get(self._buf, None)

        if len(e) > 32:
            self._buf = ''
        elif method_name:  # т.н. статические последовательности
            method = getattr(self, 'cap_' + method_name)
            method()
            self._buf = ''
        else:  # последовательности с параметрами
            for k, v in self.new_sci_seq_re_compiled:
                mo = k.match(e)
                if mo:
                    method = getattr(self, 'cap_' + v)
                    method(mo)
                    e = ''
                    self._buf = ''

            for r, f in self.esc_re:
                mo = r.match(e)
                if mo:
                    f(e, mo)
                    self._buf = ''
                    break

    def exec_single_character_command(self):
        method_name = self.control_characters[self._buf]
        method = getattr(self, 'cap_' + method_name)
        method()
        self._buf = ''

    def write(self, s):
        for i in s.decode('utf8', errors='replace'):
            if ord(i) in self.control_characters:
                self._buf = ord(i)
                self.exec_single_character_command()
            elif i == '\x1b':
                self._buf += i
            elif len(self._buf):
                self._buf += i
                self.exec_escape_sequence()
            else:
                self.echo(i)

    def dumphtml(self):
        h = self._rows
        w = self._cols
        r = ''

        # Строка, содержащая готовый к выводу символ.
        span = ''
        span_bg, span_fg = -1, -1
        for i in range(h * w):
            q, c = divmod(self._screen[i], 256 * 256 * 256)
            bg, fg = divmod(q, 16)

            # AjaxTerm использует черный цвет в качестве фона для терминала,
            # не имея при этом опции, которая позволяет его изменить. Таким
            # образом, если AjaxTerm получит предложение об отображении экрана,
            # содержащего _насыщенные_ цвета, он откорректирует это
            # предложение, заменив каждый такой цвет на его _обычный_ аналог.
            #
            # Имея, к примеру, насыщенный зеленый цвет (номер 10), посредством
            # побитового И, можно получить его обычный аналог, т.е. номер 2.
            bg &= 0x7

            if i == self._cur_y * w + self._cur_x:
                bg, fg = 1, 7

            # Если характеристики текущей ячейки совпадают с характеристиками
            # предыдущей (или предыдущих), то объединить их в группу.
            #
            # XXX: терминал не отображает последний символ в правом нижнем углу
            # (особенно это заметно при работе с Midnight Commander).
            if bg != span_bg or fg != span_fg or i + 1 == h * w:
                if len(span):
                    # Заменить каждый пробел на неразрывный пробел
                    # (non-breaking space).
                    ch = span.replace(' ', '\xa0')
                    r += '<span class="f{} b{}">{}</span>'.format(
                        span_fg,
                        span_bg,
                        html.escape(ch)
                    )
                span = ''
                span_bg, span_fg = bg, fg

            if c == 0:
                span += ' '

            span += chr(c & 0xFFFF)

            if not (i + 1) % w:
                span += '\n'

        return r
