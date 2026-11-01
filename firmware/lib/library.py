# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: MIT-0
"""The music library on the microSD card: every .flac and .mp3.

Walking the card takes a while, so the result is cached in INDEX on the card,
one tab-separated line per track, ending with a line that counts them:

    path  artist  album  title  track number
    #END  count

An index without that last line, or with the wrong count, is rebuilt. Delete
INDEX (or call Library.build) after changing the music on the card.

An album is one folder: two folders are never merged, even with the same name
or tags, and "[Disc 2]" folders stay albums of their own. FLAC tags come from
the VORBIS_COMMENT block, with the folder and file names as the fallback. MP3
tags are never read: names only, so a scan only opens the FLACs.
"""

import gc
from array import array

INDEX = "/FRISSON.IDX"
_TEMP = "/FRISSON.TMP"  # written in full, then renamed to INDEX
_END = "#END"
_SKIP_FOLDERS = ("ALBUM.PIC",)
_NO_ARTIST = ("compilations", "various artists")  # folders that aren't an artist


def _u32le(b, i):
    return b[i] | b[i + 1] << 8 | b[i + 2] << 16 | b[i + 3] << 24


def read_tags(f):
    """Return {"ARTIST": ..., "ALBUM": ..., "TITLE": ..., "TRACKNUMBER": ...}
    from an open FLAC file, or {} if it has no Vorbis comments."""
    head = f.read(4)
    if head[:3] == b"ID3":  # an ID3v2 tag in front of the FLAC stream
        rest = f.read(6)
        size = (rest[2] & 0x7F) << 21 | (rest[3] & 0x7F) << 14 | (rest[4] & 0x7F) << 7 | rest[5] & 0x7F
        f.seek(10 + size)
        head = f.read(4)
    if head != b"fLaC":
        return {}
    tags = {}
    while True:
        h = f.read(4)
        if len(h) < 4:
            return tags
        last = h[0] & 0x80
        size = h[1] << 16 | h[2] << 8 | h[3]
        if h[0] & 0x7F != 4:  # not VORBIS_COMMENT
            if last:
                return tags
            f.seek(size, 1)
            continue
        b = f.read(min(size, 8192))  # tags past 8 KB (lyrics, etc.) are skipped
        i = 4 + _u32le(b, 0)  # vendor string
        count = _u32le(b, i)
        i += 4
        for _ in range(count):
            if i + 4 > len(b):
                break
            n = _u32le(b, i)
            i += 4
            kv = b[i : i + n]
            i += n
            eq = kv.find(b"=")
            if eq > 0:
                key = kv[:eq].decode().upper()
                if key in ("ARTIST", "ALBUM", "TITLE", "TRACKNUMBER") and key not in tags:
                    tags[key] = " ".join(str(kv[eq + 1 :], "utf-8").split())
        return tags


def _clean(text):
    """Undo the "_" that file names use for spaces and for characters FAT
    can't hold. A leading "_." was "...", as in "_..I Care Because You Do"."""
    if text[:2] == "_.":
        text = "." + text[1:]
    return " ".join(text.replace("_", " ").split())


def _drop_year(text):
    """"Up" from "1998 - Up", and "Sonic Algebra" from "Sonic Algebra-2007"."""
    if text[:4].isdigit() and text[4:7] == " - ":
        return text[7:]
    if len(text) > 5 and text[-4:].isdigit() and text[-5] in " -":
        return text[:-5]
    return text


def _split_folder(folder):
    """Artist and album from one folder name, "Artist - Album", "Artist-Album"."""
    sep = " - " if " - " in folder else "-"
    if sep in folder and (sep == " - " or " " not in folder):  # not "Re-Generate Live"
        artist, album = folder.split(sep, 1)
        return _drop_year(_clean(artist)), _drop_year(_clean(album))
    return "", _clean(folder)


def _is_disc(name):
    name = name.lower()
    for word in ("disc", "disk", "cd"):
        if name.startswith(word) and name[len(word) :].strip(" -_").isdigit():
            return True
    return False


def _guess_folder(dirs):
    """Artist and album from the folders above a file."""
    disc = ""
    if len(dirs) > 1 and _is_disc(dirs[-1]):  # /Album/CD1, /Artist/Album/CD1
        disc = " " + _clean(dirs[-1])
        dirs = dirs[:-1]
    if not dirs:
        return "", "Loose Files"
    if len(dirs) == 1:
        artist, album = _split_folder(dirs[0])
    else:
        artist, album = _clean(dirs[0]), _clean(dirs[-1])
    if artist.lower() in _NO_ARTIST:
        artist = ""
    return artist, album + disc


def _guess_file(stem):
    """Disc, track number and title from a file name without its extension:
    "01 Title", "01 - Title", "01-title", "1-01 Title", "101-artist-title",
    "A1 - Title". A name without a number gets (0, 0, name)."""
    i = 0
    while i < len(stem) and stem[i].isdigit():
        i += 1
    disc = number = 0
    if i == 1 and stem[1:2] == "-" and stem[2:4].isdigit():  # 1-01
        disc, number, i = int(stem[0]), int(stem[2:4]), 4
    elif i == 3 and stem[3:4] == "-":  # 101-: disc 1, track 01
        disc, number = int(stem[0]), int(stem[1:3])
    elif i in (1, 2):
        number = int(stem[:i])
    elif i == 0 and stem[:1] in ("A", "B", "C", "D") and stem[1:2].isdigit():  # vinyl side A1
        i = 2
        while i < len(stem) and stem[i].isdigit():
            i += 1
        disc, number = ord(stem[0]) - 64, int(stem[1:i])
    else:
        i = 0
    rest = stem[i:]
    if i and rest and rest[0] not in " -_" and rest[:2] != ". ":  # "3rd Planet", "4.27"
        return 0, 0, _clean(stem)
    if i:
        rest = rest.lstrip(" -_")
        if rest[:2] == ". ":
            rest = rest[2:]
        if " " not in rest and "_" in rest and "-" in rest:  # artist_name-title_words
            rest = rest.split("-", 1)[1]
    return disc, number, _clean(rest) or _clean(stem)


def _album_names(tracks):
    """Artist and album of one folder's tracks: no artist if they differ."""
    artist = tracks[0][3]
    for t in tracks:
        if t[3] != artist:
            artist = ""
            break
    return artist, tracks[0][4]


class _Tracks:
    """The tracks, read from the index file when asked for.

    One tuple and four strings per track made 5700 tracks a heap that took
    125 ms to collect, and a half of 24/96 lasts 64 ms, so each collection was
    an underrun. The text alone in RAM still took 73 ms: the collector scans it
    for pointers. Only the line offsets stay in RAM: 29 ms (10-31)."""

    def __init__(self, raw, starts):
        self._raw = raw  # the file's bytes, until use_file
        self._file = None
        self._starts = starts  # where each line starts, and where the last ends

    def use_file(self, file):
        self._file = file
        self._raw = None

    def __len__(self):
        return len(self._starts) - 1

    def __getitem__(self, i):
        a = self._starts[i]
        b = self._starts[i + 1] - 1
        if self._file:
            self._file.seek(a)
            line = self._file.read(b - a)
        else:
            line = self._raw[a:b]
        p = str(line, "utf-8").split("\t")
        if len(p) != 5:
            raise ValueError("index damaged")
        return p[0], p[1], p[2], p[3], int(p[4])


class Library:
    """Tracks and albums.

    tracks: (path, artist, album, title, number) by index, in order. The tracks
            of a folder are together, by disc, track number and path
    albums: list of (artist, album, range of track indexes), one per folder, by
            artist (or album, if it has no artist), album, path. artist is ""
            if the tracks of the folder have different artists
    """

    def __init__(self, vfs, progress=None):
        self.vfs = vfs
        try:
            self._load()
        except (OSError, ValueError):  # missing, or cut short by a power loss
            self.build(progress)
        gc.collect()

    def _load(self):
        """Read INDEX, or raise OSError or ValueError if it isn't complete.

        The whole file is read at once and split by hand: iterating a file
        reads one byte at a time. The index is sorted already, and sorting 5700
        tracks here would take too long (see build)."""
        size = self.vfs.stat(INDEX)[6]
        with self.vfs.open(INDEX, "rb") as f:
            raw = f.read(size)
        cut = raw.rfind(b"\n", 0, len(raw) - 1) + 1
        last = str(raw[cut:], "utf-8").split("\t")
        if len(last) != 2 or last[0] != _END or last[1][-1:] != "\n":
            raise ValueError("index incomplete")
        count = int(last[1])
        starts = array("I", [0]) * (count + 1)
        i = n = 0
        while i < cut:
            if n == count:
                raise ValueError("index damaged")
            starts[n] = i
            n += 1
            i = raw.find(b"\n", i) + 1
        if n != count:
            raise ValueError("index damaged")
        starts[n] = cut
        tracks = _Tracks(raw, starts)
        self._set(tracks)  # reads every line, so damage shows here
        tracks.use_file(self.vfs.open(INDEX, "rb"))

    def build(self, progress=None):
        """Walk the card, read the FLACs' tags and rewrite INDEX."""
        paths = []
        self._walk("", paths)
        folders = {}
        for n, path in enumerate(paths):
            if progress:
                progress(n, len(paths))
            cut = path.rfind("/")
            folder = path[:cut]
            name = path[cut + 1 :]
            tags = {}
            if name.lower().endswith(".flac"):
                try:
                    with self.vfs.open(path, "rb") as f:
                        tags = read_tags(f)
                except (OSError, ValueError, UnicodeError, IndexError):
                    tags = {}
            artist, album = _guess_folder(folder.split("/")[1:])
            disc, number, title = _guess_file(name[: name.rfind(".")])
            tag = tags.get("TRACKNUMBER", "").split("/")[0]
            if folder not in folders:
                folders[folder] = []
            folders[folder].append((
                disc,
                int(tag) if tag.isdigit() else number,
                path,
                tags.get("ARTIST") or artist,
                tags.get("ALBUM") or album,
                tags.get("TITLE") or title,
            ))
        # list.sort is a quicksort that takes n^2 compares on sorted input and
        # calls key= on every compare, so sort the short lists, without key=
        order = []
        for folder, tracks in folders.items():
            tracks.sort()
            artist, album = _album_names(tracks)
            order.append(((artist or album).lower(), album.lower(), folder))
        order.sort()
        tracks = []
        for _, _, folder in order:
            for t in folders.pop(folder):
                tracks.append((t[2], t[3], t[4], t[5], t[1]))
        try:
            self._write(tracks)
            self._load()  # one path for names shared in RAM, and proves the file
        except (OSError, ValueError) as e:  # card full or read only: rescan next boot
            print("index not saved", e)
            self._set(tracks)

    def _write(self, tracks):
        """Write the whole index to a temporary file, then put it in place."""
        if not tracks:  # nothing to cache
            try:
                self.vfs.remove(INDEX)
            except OSError:
                pass
            return
        with self.vfs.open(_TEMP, "w") as f:
            for t in tracks:
                f.write("%s\t%s\t%s\t%s\t%d\n" % t)
            f.write("%s\t%d\n" % (_END, len(tracks)))
        self.vfs.rename(_TEMP, INDEX)

    def _walk(self, folder, paths):
        try:
            entries = list(self.vfs.ilistdir(folder or "/"))
        except OSError as e:  # "/Sven Väth" lists from the root but won't open (10-31)
            print("skipped", folder, e)
            return
        for name, kind, *_ in entries:
            if name.startswith("."):  # also skips the macOS ._ files
                continue
            path = folder + "/" + name
            if kind == 0x4000:
                if name not in _SKIP_FOLDERS:
                    self._walk(path, paths)
            elif name.lower().endswith((".flac", ".mp3")):
                paths.append(path)

    def _set(self, tracks):
        """Group tracks, already in order, into albums, one per folder."""
        self.tracks = tracks
        self.albums = []
        folder = artist = album = None
        first = 0
        for i in range(len(tracks)):
            t = tracks[i]
            path = t[0]
            f = path[: path.rfind("/")]
            if f != folder:
                if folder is not None:
                    self.albums.append((artist, album, range(first, i)))
                folder, first, artist, album = f, i, t[1], t[2]
            elif artist and artist != t[1]:
                artist = ""
        if folder is not None:
            self.albums.append((artist, album, range(first, len(tracks))))
