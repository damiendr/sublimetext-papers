"""
A SublimeText 3 plugin to navigate and insert citekeys from the user's
reference manager (here Mekentosj's Papers v2).

Instructions:

First edit `basepath` below to point at the root folder of your Papers2
database.

Add this script to your User packages and add a keyboard shortcut as
follows:
    { "keys": ["ctrl+&"], "command": "insert_citation" }

Note: to access Papers' database this plugin needs the sqlite3 library,
which is part of the standard Python distribution but is sadly not
included in SublimeText's lightweight runtime. Thus the relevant files
(_sqlite3.so, etc.) must be copied from a binary-compatible full Python
install into SublimeText's Packages directory. Same goes for pyparsing.
TODO look into package managers for SublimeText so that we can package
this a bit more cleanly.
"""

# Edit with your Papers2 database path:
basepath = "/Users/username/Papers2/"
dbpath = basepath + "Library.papers2/Database.papersdb"

import sublime, sublime_plugin
from pyparsing import (Word, alphas, nums, Literal, Group,
    OneOrMore, Optional, Combine, Empty)
import sys
import os
import sqlite3
import zlib
import subprocess


# =============================================================
# Universal Citekey Algorithm
# For details and rationale:
# http://support.mekentosj.com/kb/cite-write-your-manuscripts-and-essays-with-citations/universal-citekey
# =============================================================

alphabet = [chr(x) for x in range(ord('a'), ord('z')+1)]
title_suffix = [chr(x) for x in range(ord('t'), ord('w')+1)]
doi_suffix = [chr(x) for x in range(ord('b'), ord('k')+1)]

def gen_crc(s):
    # Re-interpret the signed int returned by zlib.crc32 as an unsigned int:
    return zlib.crc32(bytes(s, "UTF-8")) & 0xffffffff

def gen_hash(text, suffixes):
    n1 = gen_crc(text)
    n2 = n1 % (len(alphabet) * len(suffixes))
    n3 = n2 // len(alphabet)
    n4 = n2 % len(alphabet)
    return "%s%s" % (suffixes[n3], alphabet[n4])

def gen_title_hash(title):
    if title is None: return None
    return gen_hash(title, title_suffix)

def gen_doi_hash(doi):
    if doi is None: return None
    return gen_hash(doi, doi_suffix)

def gen_citekey(base, year, doi, title):
    if doi:
        return "%s:%s%s" % (base, year, gen_doi_hash(doi))
    else:
        return "%s:%s%s" % (base, year, gen_title_hash(title))


# =============================================================
# Papers Database Access
# =============================================================

def list_citations(db):
    candidates = db.execute(
        "SELECT author_year_string, attributed_title, canonical_title, doi, "
        "citekey_base, publication_date FROM Publication")
    for author_year, title, canonical, doi, base, date in candidates:
        try:
            year = date[2:6]
            citekey = gen_citekey(base, year, doi, canonical)
        except:
            pass
        else:
            yield "%s %s" % (author_year, title), citekey

def split_key(citekey):
    base, suffix = citekey.split(":")
    year = suffix[:4]
    citehash = suffix[4:]
    return base, year, citehash

def find_pdf(db, citekey):
    # Split the citekey into <base>:<year><citehash>
    base, year, citehash = split_key(citekey)
    
    # Papers does not store the hash part of the citekey in its database.
    # First do a partial match on the base (author) and year:
    candidates = db.execute(
        "SELECT ROWID, canonical_title, doi FROM Publication "
        "WHERE citekey_base = ? AND substr(publication_date, 3, 4) == ?",
        (base, year))
    
    # Now generate hashes for these candidates and look for an exact match:
    for (rowid, title, doi) in candidates:
        if (citehash == gen_title_hash(title) or
            citehash == gen_doi_hash(doi)):
            # Got a match for the complete citekey!
            # Let's see if we can find any PDF files for this paper:
            pdfs = db.execute("SELECT Path FROM PDF WHERE object_id = ?",
                                (rowid,))
            # Return the first PDF entry:
            for (pdf_path,) in pdfs:
                return os.path.join(basepath, pdf_path)
            # If no PDF was found, move on to next matching paper:
            # there might be duplicates entries with the same hash.

    raise Exception("No matching PDF found for %s" % citekey)


def get_citations():
    """
    Called by the SublimeText command to retrieve the list of citations.
    Returns a list of (reference, citekey) pairs.
    """
    db = sqlite3.connect(dbpath)
    try:
        return reversed(list(list_citations(db)))
    finally:
        db.close()


# Find out how to open a file URL with the default app:
import platform
if platform.system() == "Darwin":
    open_cmd = "open"
elif platform.system() == "Linux":
    open_cmd = "xdg-open"
else: # Windows
    open_cmd = "start"


def open_citekey(citekey):
    """
    Called by the SublimeText command to open the file associated with
    a particular citekey.
    """
    db = sqlite3.connect(dbpath)
    try:
        fpath = find_pdf(db, citekey)
        print(fpath)
    finally:
        db.close()
    subprocess.call([open_cmd, fpath])


# =============================================================
# Sublime Text Bibliography Command
# =============================================================

# PyParsing grammar for multiple citekeys within curly brackets:
add_loc = lambda locn, tokens: (locn, tokens[0])
citekey_expr = Combine(Word(alphas) + ":" + Word(nums, exact=4) + Word(alphas, exact=2))
citations_expr = Literal("{").suppress() \
                    + OneOrMore(citekey_expr.setParseAction(add_loc) \
                    + Optional(",").suppress()) + Literal("}").suppress()

def format_citekeys(keys):
    return "{%s}" % ", ".join(keys)

def format_markdown(keys):
    return ", ".join("[%s](papers2://publication/citekey/%s)" % (key, key) for key in keys)

def parse_line(text):
    results = citations_expr.scanString(text)
    for matches, start, end in results:
        print(start, end)
        for loc, key in matches:
            print(loc, key)


class InsertCitationCommand(sublime_plugin.WindowCommand):

    def run(self):
        """
        Main entry point for the insert_citation command.
        """
        # Fetch the citations from the reference manager:
        citations, citekeys = zip(*get_citations())
        self.citations = list(citations)
        self.citekeys = citekeys
        
        # Do we already have one or more citekeys under the cursor?
        self.citekey, self.group, self.location = self.citekeys_at_cursor()
        if self.citekey:
            # We do. Let's add an option to open the corresponding PDF.
            self.commands = ["%s: Open PDF" % self.citekey,
                             "-----------------"]
        else:
            # Nope. Just show the list of available citations.
            self.commands = []

        self.window.show_quick_panel(self.commands + self.citations,
                                     self.on_citekey)

    def on_citekey(self, item):
        """
        Called when the user selects an entry in the quick selection menu.
        """
        if item >= len(self.commands):
            # Here: the user selected a citekey. Let's fetch the details:
            self.citekey = self.citekeys[item - len(self.commands)]

            # Add the citekey to the current citation group, and make
            # sure it stays sorted:
            if self.citekey not in self.group:
                self.group.append(self.citekey)
                self.group = sorted(
                                set(self.group),
                                key=lambda k: split_key(k)[1])

            # Show another menu to view or insert the citations:
            self.commands = ["Insert {%s}" % ", ".join(self.group),
                             "%s: Open PDF" % self.citekey,
                             "Insert Markdown link",
                             "-----------------"]
            sublime.set_timeout(
                lambda: self.window.show_quick_panel(
                        self.commands + self.citations,
                        self.on_citekey),
                10)

        elif self.commands[item].startswith("Insert"):
            if self.commands[item].startswith("Insert Markdown"):
                citetext = format_markdown(self.group)
            else:
                citetext = format_citekeys(self.group)
            # Here: let's insert the selected citation(s):
            sublime.set_timeout(
                lambda: self.window.run_command(
                    "insert_citation_text",
                    {"citetext":citetext, "loc":self.location}),
                10)

        elif self.commands[item].endswith("Open PDF"):
            # Here: let's open the corresponding PDF file:
            open_citekey(self.citekey)


    def citekeys_at_cursor(self):
        view = self.window.active_view()
        locs = view.sel()
        loc = locs[0]
        line = view.line(loc.a)
        text = view.substr(line)
        offset = loc.a - line.a
        for group, start, end in citations_expr.scanString(text):
            keys = [str(key) for pos, key in group]
            if start <= offset <= end:
                for pos, citekey in group:
                    if pos <= offset <= pos + len(citekey):
                        return citekey, keys, (start + line.a, end + line.a)
        return None, [], (loc.a, loc.b)


class InsertCitationTextCommand(sublime_plugin.TextCommand):
    """
    Helper command to perform the actual modification of the
    current text buffer. SublimeText 3 requires this for proper
    undo/redo support.
    """
    def run(self, edit, citetext, loc):
        print(edit, citetext, loc)
        loc = sublime.Region(*loc)
        if loc.empty():
            self.view.insert(edit, loc.a, citetext)
        else:
            self.view.replace(edit, loc, citetext)
