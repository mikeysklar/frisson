# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: MIT-0
"""Frisson's menus: what the keys do, and what the screen shows.

  ANO wheel     move the highlight (volume on the now playing screen)
  ANO select    open the artist or album, or play the track
                (an artist with one album opens straight to its tracks)
  ANO up        back, to albums then artists
  ANO down      now playing
  ANO left/right  page down / page up
  NeoKey 1 to 4 play, pause, reverse, forward
                (reverse restarts the track, or goes to the previous one
                in its first 3 seconds)
  hold NeoKey 3 / 4  rewind / fast-forward

No hardware here. Menus takes events from keys.Keys.poll(), calls a player
object (see frisson.py), and the screen reads view(). `version` goes up
whenever the view changes. Call tick() from the main loop to time out notices.
"""

import time

ROWS = 4  # list rows on the screen
WIDTH = 19  # characters per row after a tag, 23 without (10 px each)
NOTICE_S = 3  # how long a message replaces the footer
VARIOUS = "Various"  # the row of albums with no one artist


def playable(path):
    """Can the player decode this file? The index only lists FLAC and MP3."""
    return path[-5:].lower() == ".flac" or path[-4:].lower() == ".mp3"


class Screen:
    def __init__(self, kind, title, items, cursor=0):
        self.kind = kind  # "albums" (also the artist list), "tracks", "now"
        self.title = title
        self.items = items  # list of (tag, text, target)
        self.cursor = cursor
        self.top = 0


class Menus:
    def __init__(self, library, player):
        self.lib = library
        self.player = player
        self.version = 0
        self.notice = ""  # replaces the footer until notice_until
        self.notice_until = 0
        self.stack = [self._artists()]

    # screens

    def _artists(self):
        """Built once, at start, and kept: the names are the library's own
        strings. One row per artist, target ("artist", first, end), the range
        of lib.albums holding their albums. Albums with no artist (compilations,
        mixed folders) sort in among the artists by their own names, so they
        are gathered into one row at the top, the shortest reach."""
        albums = self.lib.albums
        items = []
        various = None
        key = None
        for i, a in enumerate(albums):
            if not a[0]:
                various = (various[0] if various else i, i + 1)
            elif a[0].lower() == key:
                items[-1] = ("", items[-1][1], ("artist", items[-1][2][1], i + 1))
            else:
                key = a[0].lower()
                items.append(("", a[0], ("artist", i, i + 1)))
        if various:
            items.insert(0, ("", VARIOUS, ("artist",) + various))
        return Screen("albums", "ARTISTS  %d" % len(items), items)

    def _albums(self, first, end):
        """One artist's albums: the range can hold others' no-artist albums."""
        albums = self.lib.albums
        key = albums[first][0].lower()
        items = [("", albums[i][1], ("album", i)) for i in range(first, end)
                 if albums[i][0].lower() == key]
        return Screen("albums", (albums[first][0] or VARIOUS).upper(), items)

    def _tracks(self, album, cursor=0):
        artist, title, indexes = self.lib.albums[album]
        items = []
        for n, i in enumerate(indexes):
            t = self.lib.tracks[i]
            items.append(("%02d" % (t[4] or n + 1), t[3], ("track", album, n)))
        return Screen("tracks", title.upper(), items, cursor)

    @property
    def top(self):
        return self.stack[-1]

    def _push(self, screen):
        self.stack.append(screen)
        self.version += 1

    def _back(self):
        if len(self.stack) > 1:
            self.stack.pop()
            self.version += 1

    def _move(self, steps):
        s = self.top
        if not s.items:
            return
        cursor = max(0, min(len(s.items) - 1, s.cursor + steps))
        if cursor != s.cursor:
            s.cursor = cursor
            self.version += 1

    def _open(self, target):
        kind = target[0]
        if kind == "artist":
            _, first, end = target
            self._push(self._albums(first, end) if end - first > 1 else self._tracks(first))
        elif kind == "album":
            self._push(self._tracks(target[1]))
        elif kind == "track":
            _, album, n = target
            if self._play(self.lib.albums[album][2], n):
                self.now_playing()

    def _play(self, queue, n, skip=False):
        """Ask the player for queue[n]; if it refuses, say so. True if it plays."""
        if self.player.play(queue, n, skip):
            return True
        self.notify("can't play that file")
        return False

    def notify(self, text):
        self.notice = text
        self.notice_until = time.monotonic() + NOTICE_S
        self.version += 1

    def tick(self):
        """Report a file the player skipped, and time out the notice."""
        if self.player.skipped:
            self.player.skipped = False
            self.notify("skipped a bad file")
        elif self.notice and time.monotonic() >= self.notice_until:
            self.notice = ""
            self.version += 1

    def now_playing(self):
        if self.top.kind != "now":
            self._push(Screen("now", "NOW PLAYING", []))
        else:
            self.version += 1

    # input

    def handle(self, kind, value):
        """Act on one event from keys.Keys.poll()."""
        s = self.top
        p = self.player
        if kind == "key":
            if value == 0:
                if p.paused:
                    p.resume()
                elif not p.playing:
                    self._play_highlighted()
            elif value == 1:
                p.pause()
            elif value == 2:
                p.reverse()
            elif value == 3:
                p.forward()
            self.version += 1
        elif kind == "hold":
            p.scan(-1 if value == 2 else 1)
        elif kind == "wheel":
            if s.kind == "now":
                p.volume(value)
                self.version += 1
            else:
                self._move(value)
        elif kind == "button":
            if value == "select" and s.items:
                self._open(s.items[s.cursor][2])
            elif value == "up":
                self._back()
            elif value == "down" and p.current is not None:
                self.now_playing()
            elif value == "left":  # flipped to suit how the ANO is mounted
                self._move(ROWS)
            elif value == "right":
                self._move(-ROWS)

    def _play_highlighted(self):
        s = self.top
        if not s.items:
            return
        target = s.items[s.cursor][2]
        if target[0] in ("artist", "album"):  # an artist plays their first album
            self._play(self.lib.albums[target[1]][2], 0, True)
        elif target[0] == "track":
            self._open(target)

    # output

    def view(self):
        """What to draw: a dict with title, rows [(tag, text)], sel (the
        highlighted row or -1), right (text at the right of the title) and
        footer."""
        s = self.top
        p = self.player
        footer = ""
        if p.current is not None:
            t = self.lib.tracks[p.current]
            footer = "%s %s" % ("||" if p.paused else ">", t[3])
        footer = self.notice or footer
        if s.kind == "now":
            return self._view_now(footer)
        if s.cursor < s.top:
            s.top = s.cursor
        elif s.cursor >= s.top + ROWS:
            s.top = s.cursor - ROWS + 1
        rows = [(tag, text[:WIDTH if tag else WIDTH + 4]) for tag, text, _ in s.items[s.top : s.top + ROWS]]
        title = s.title
        right = "%d/%d" % (s.cursor + 1, len(s.items)) if s.items else ""
        return {"kind": s.kind, "title": title, "right": right, "rows": rows,
                "sel": s.cursor - s.top if s.items else -1, "footer": footer}

    def _view_now(self, footer):
        p = self.player
        if p.current is None:
            rows = [("", "nothing playing")]
        else:
            path, artist, album, title, number = self.lib.tracks[p.current]
            n, total = p.position()
            rows = [
                ("%02d" % (number or n), artist[:WIDTH]),
                ("", album[:WIDTH + 4]),
                ("", title[:WIDTH + 4]),
                ("", "%d/%d  %s  %d dB" % (n, total, "paused" if p.paused else "playing", p.db)),
            ]
        right = p.format if p.current is not None else ""
        return {"kind": "now", "title": "NOW PLAYING", "right": right, "rows": rows,
                "sel": -1, "footer": self.notice or "wheel volume   up back"}

    def text(self):
        """The view as plain text, for the serial console."""
        v = self.view()
        out = ["== %s  %s" % (v["title"], v["right"])]
        for i, (tag, text) in enumerate(v["rows"]):
            out.append("%s %3s %s" % (">" if i == v["sel"] else " ", tag, text))
        if v["footer"]:
            out.append("-- " + v["footer"])
        return "\n".join(out)
