"""Render API payloads as plain text or XML.

JSON goes through Flask's ``jsonify``; these cover the other two formats the
public location API offers. Both walk the payload generically, so any field
added to ``services.build_location_payload`` shows up without changes here.
"""

import xml.etree.ElementTree as ElementTree

SUPPORTED_FORMATS = ("json", "text", "xml")

MIME_TYPES = {
    "json": "application/json",
    "text": "text/plain; charset=utf-8",
    "xml": "application/xml; charset=utf-8",
}


def _scalar(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# --------------------------------------------------------------------------
# Plain text
# --------------------------------------------------------------------------


def to_text(payload):
    """Indented ``key: value`` lines, one nesting level per two spaces."""
    lines = []
    _text_lines(payload, 0, lines)
    return "\n".join(lines) + "\n"


def _text_lines(value, depth, lines):
    indent = "  " * depth
    items = value.items() if isinstance(value, dict) else enumerate(value, 1)
    for key, item in items:
        label = key.replace("_", " ") if isinstance(key, str) else "#%d" % key
        if isinstance(item, (dict, list)):
            if not item:
                lines.append("%s%s: -" % (indent, label))
                continue
            lines.append("%s%s:" % (indent, label))
            _text_lines(item, depth + 1, lines)
        else:
            lines.append("%s%s: %s" % (indent, label, _scalar(item) or "-"))


# --------------------------------------------------------------------------
# XML
# --------------------------------------------------------------------------


def to_xml(payload, root="response"):
    element = ElementTree.Element(root)
    _xml_fill(element, payload)
    ElementTree.indent(element)
    return ElementTree.tostring(element, encoding="unicode", xml_declaration=True) + "\n"


def _xml_fill(element, value):
    if isinstance(value, dict):
        for key, item in value.items():
            _xml_fill(ElementTree.SubElement(element, _xml_tag(key)), item)
    elif isinstance(value, list):
        for item in value:
            _xml_fill(ElementTree.SubElement(element, "item"), item)
    else:
        element.text = _scalar(value)


def _xml_tag(key):
    # Payload keys are snake_case identifiers already; guard anyway so a stray
    # key can never produce malformed XML.
    tag = "".join(ch if ch.isalnum() or ch in "_-." else "_" for ch in str(key))
    if not tag or not (tag[0].isalpha() or tag[0] == "_"):
        tag = "_" + tag
    return tag
