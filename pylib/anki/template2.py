# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""
This file contains some code related to templates that is not directly
connected to pystache. It may be renamed in the future.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Tuple

import anki
from anki.hooks import addHook, runFilter
from anki.lang import _
from anki.sound import stripSounds
from anki.utils import stripHTML, stripHTMLMedia


def render_from_field_map(
    qfmt: str, afmt: str, fields: Dict[str, str], card_ord: int
) -> Tuple[str, str]:
    "Renders the provided templates, returning rendered q & a text."
    # question
    format = re.sub("{{(?!type:)(.*?)cloze:", r"{{\1cq-%d:" % (card_ord + 1), qfmt)
    format = format.replace("<%cloze:", "<%%cq:%d:" % (card_ord + 1))
    qtext = anki.template.render(format, fields)

    # answer
    format = re.sub("{{(.*?)cloze:", r"{{\1ca-%d:" % (card_ord + 1), afmt)
    format = format.replace("<%cloze:", "<%%ca:%d:" % (card_ord + 1))
    fields["FrontSide"] = stripSounds(qtext)
    atext = anki.template.render(format, fields)

    return qtext, atext


def field_is_not_empty(field_text: str) -> bool:
    # fixme: this is an overkill way of preventing a field with only
    # a <br> or <div> from appearing non-empty
    field_text = stripHTMLMedia(field_text)

    return field_text.strip() != ""


# Filters
##########################################################################


def apply_field_filters(
    field_name: str, field_text: str, fields: Dict[str, str], filters: List[str]
) -> str:
    """Apply filters to field text, returning modified text."""
    _sort_filters(filters)

    for filter in filters:
        # built-in modifiers
        if filter == "text":
            # strip html
            field_text = stripHTML(field_text) if field_text else ""
        elif filter == "type":
            # type answer field; convert it to [[type:...]] for the gui code
            # to process
            field_text = "[[type:%s]]" % field_name
        elif filter.startswith("cq-") or filter.startswith("ca-"):
            # cloze deletion
            filter, extra = filter.split("-")
            field_text = (
                _clozeText(field_text, extra, filter[1]) if field_text and extra else ""
            )
        else:
            # the second and fifth arguments are no longer used
            field_text = runFilter(
                "fmod_" + filter, field_text, "", fields, field_name, ""
            )
            if not isinstance(field_text, str):
                return "{field modifier '%s' on template invalid}" % filter
    return field_text


def _sort_filters(filters: List[str]):
    "Mutate the list of filters into the correct order."

    # the filter closest to the field name is applied first
    filters.reverse()
    # Since 'text:' and other mods can affect html on which Anki relies to
    # process clozes, we need to make sure clozes are always
    # treated after all the other mods, regardless of how they're specified
    # in the template, so that {{cloze:text: == {{text:cloze:
    # For type:, we return directly since no other mod than cloze (or other
    # pre-defined mods) can be present and those are treated separately
    filters.sort(key=lambda s: not s == "type")


# Matches a {{c123::clozed-out text::hint}} Cloze deletion, case-insensitively.
# The regex should be interpolated with a regex number and creates the following
# named groups:
#   - tag: The lowercase or uppercase 'c' letter opening the Cloze.
#   - content: Clozed-out content.
#   - hint: Cloze hint, if provided.
clozeReg = r"(?si)\{\{(?P<tag>c)%s::(?P<content>.*?)(::(?P<hint>.*?))?\}\}"

# Constants referring to group names within clozeReg.
CLOZE_REGEX_MATCH_GROUP_TAG = "tag"
CLOZE_REGEX_MATCH_GROUP_CONTENT = "content"
CLOZE_REGEX_MATCH_GROUP_HINT = "hint"


def _clozeText(txt: str, ord: str, type: str) -> str:
    """Process the given Cloze deletion within the given template."""
    reg = clozeReg
    currentRegex = clozeReg % ord
    if not re.search(currentRegex, txt):
        # No Cloze deletion was found in txt.
        return ""
    txt = _removeFormattingFromMathjax(txt, ord)

    def repl(m):
        # replace chosen cloze with type
        if type == "q":
            if m.group(CLOZE_REGEX_MATCH_GROUP_HINT):
                buf = "[%s]" % m.group(CLOZE_REGEX_MATCH_GROUP_HINT)
            else:
                buf = "[...]"
        else:
            buf = m.group(CLOZE_REGEX_MATCH_GROUP_CONTENT)
        # uppercase = no formatting
        if m.group(CLOZE_REGEX_MATCH_GROUP_TAG) == "c":
            buf = "<span class=cloze>%s</span>" % buf
        return buf

    txt = re.sub(currentRegex, repl, txt)
    # and display other clozes normally
    return re.sub(reg % r"\d+", "\\2", txt)


def _removeFormattingFromMathjax(txt, ord) -> str:
    """Marks all clozes within MathJax to prevent formatting them.

    Active Cloze deletions within MathJax should not be wrapped inside
    a Cloze <span>, as that would interfere with MathJax.

    This method finds all Cloze deletions number `ord` in `txt` which are
    inside MathJax inline or display formulas, and replaces their opening
    '{{c123' with a '{{C123'. The clozeText method interprets the upper-case
    C as "don't wrap this Cloze in a <span>".
    """
    creg = clozeReg.replace("(?si)", "")

    # Scan the string left to right.
    # After a MathJax opening - \( or \[ - flip in_mathjax to True.
    # After a MathJax closing - \) or \] - flip in_mathjax to False.
    # When a Cloze pattern number `ord` is found and we are in MathJax,
    # replace its '{{c' with '{{C'.
    #
    # TODO: Report mismatching opens/closes - e.g. '\(\]'
    # TODO: Report errors in this method better than printing to stdout.
    # flags in middle of expression deprecated
    in_mathjax = False

    def replace(match):
        nonlocal in_mathjax
        if match.group("mathjax_open"):
            if in_mathjax:
                print("MathJax opening found while already in MathJax")
            in_mathjax = True
        elif match.group("mathjax_close"):
            if not in_mathjax:
                print("MathJax close found while not in MathJax")
            in_mathjax = False
        elif match.group("cloze"):
            if in_mathjax:
                return match.group(0).replace(
                    "{{c{}::".format(ord), "{{C{}::".format(ord)
                )
        else:
            print("Unexpected: no expected capture group is present")
        return match.group(0)

    # The following regex matches one of:
    #  -  MathJax opening
    #  -  MathJax close
    #  -  Cloze deletion number `ord`
    return re.sub(
        r"(?si)"
        r"(?P<mathjax_open>\\[([])|"
        r"(?P<mathjax_close>\\[\])])|"
        r"(?P<cloze>" + (creg % ord) + ")",
        replace,
        txt,
    )


def expand_clozes(string: str) -> List[str]:
    "Render all clozes in string."
    ords = set(re.findall(r"{{c(\d+)::.+?}}", string))
    strings = []

    def qrepl(m):
        if m.group(CLOZE_REGEX_MATCH_GROUP_HINT):
            return "[%s]" % m.group(CLOZE_REGEX_MATCH_GROUP_HINT)
        else:
            return "[...]"

    def arepl(m):
        return m.group(CLOZE_REGEX_MATCH_GROUP_CONTENT)

    for ord in ords:
        s = re.sub(clozeReg % ord, qrepl, string)
        s = re.sub(clozeReg % ".+?", arepl, s)
        strings.append(s)
    strings.append(re.sub(clozeReg % ".+?", arepl, string))

    return strings


def hint(txt, extra, context, tag, fullname) -> str:
    if not txt.strip():
        return ""
    # random id
    domid = "hint%d" % id(txt)
    return """
<a class=hint href="#"
onclick="this.style.display='none';document.getElementById('%s').style.display='block';return false;">
%s</a><div id="%s" class=hint style="display: none">%s</div>
""" % (
        domid,
        _("Show %s") % tag,
        domid,
        txt,
    )


FURIGANA_RE = r" ?([^ >]+?)\[(.+?)\]"
RUBY_REPL = r"<ruby><rb>\1</rb><rt>\2</rt></ruby>"


def replace_if_not_audio(repl: str) -> Callable[[Any], Any]:
    def func(match):
        if match.group(2).startswith("sound:"):
            # return without modification
            return match.group(0)
        else:
            return re.sub(FURIGANA_RE, repl, match.group(0))

    return func


def without_nbsp(s: str) -> str:
    return s.replace("&nbsp;", " ")


def kanji(txt: str, *args) -> str:
    return re.sub(FURIGANA_RE, replace_if_not_audio(r"\1"), without_nbsp(txt))


def kana(txt: str, *args) -> str:
    return re.sub(FURIGANA_RE, replace_if_not_audio(r"\2"), without_nbsp(txt))


def furigana(txt: str, *args) -> str:
    return re.sub(FURIGANA_RE, replace_if_not_audio(RUBY_REPL), without_nbsp(txt))


addHook("fmod_hint", hint)
addHook("fmod_kanji", kanji)
addHook("fmod_kana", kana)
addHook("fmod_furigana", furigana)
