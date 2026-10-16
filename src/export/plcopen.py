"""PLCopen TC6 XML (v2.01) export of plc/, and the way back.

`export()` writes the data types, the function blocks, functions and programs
and one configuration with a 200 ms cyclic task calling the five programs in
scan order, with the global variable lists and their `AT` addresses. Bodies go
in as Structured Text, verbatim.

`to_st()` reads such a file back into .st source. It exists so CI can prove the
export is complete: the source regenerated from the XML has to load in the
interpreter with the same globals and addresses and compile clean with RuSTy.

The XML validates against the TC6 v2.01 schema. It has not been imported into
CODESYS, TwinCAT or any other vendor tool; see docs/plcopen.md.

Declarations are read from the source text, one declaration per line, which is
how plc/ is written; `export()` cross-checks every name it finds against the
parser in src/iec61131 so a declaration the line reader misses is an error,
not a silent omission.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Sequence, Tuple

NS = "http://www.plcopen.org/xml/tc6_0201"
XHTML = "http://www.w3.org/1999/xhtml"

ELEMENTARY = {
    "BOOL", "SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT",
    "BYTE", "WORD", "DWORD", "LWORD", "REAL", "LREAL", "TIME", "DATE", "TOD", "DT",
}

BLOCK_TAGS = {
    "VAR_INPUT": "inputVars",
    "VAR_OUTPUT": "outputVars",
    "VAR_IN_OUT": "inOutVars",
    "VAR": "localVars",
    "VAR_TEMP": "tempVars",
    "VAR_EXTERNAL": "externalVars",
}
TAG_BLOCKS = {tag: kind for kind, tag in BLOCK_TAGS.items()}
POU_TYPES = {"FUNCTION_BLOCK": "functionBlock", "FUNCTION": "function", "PROGRAM": "program"}
POU_KINDS = {value: key for key, value in POU_TYPES.items()}

_DECL = re.compile(
    r"^\s*(?P<name>[A-Za-z_]\w*)\s*(?:AT\s+(?P<addr>%[A-Za-z]+[\d.]+))?\s*:\s*"
    r"(?P<type>[^:;=]+?)\s*(?::=\s*(?P<init>[^;]+?))?\s*;\s*$"
)
_ARRAY = re.compile(r"^ARRAY\s*\[(?P<dims>[^\]]+)\]\s*OF\s+(?P<elem>.+)$", re.I)
_STRING = re.compile(r"^(?P<kind>W?STRING)\s*(?:\[\s*(?P<len>\d+)\s*\])?$", re.I)


# -- reading the source ---------------------------------------------------------


def blank_comments(text: str) -> str:
    """Comments replaced by spaces, newlines kept, so offsets still line up."""
    out = list(text)
    i, depth, line_comment = 0, 0, False
    while i < len(text):
        pair = text[i : i + 2]
        if line_comment:
            if text[i] == "\n":
                line_comment = False
            else:
                out[i] = " "
            i += 1
            continue
        if depth == 0 and pair == "//":
            line_comment = True
            out[i] = out[i + 1] = " "
            i += 2
            continue
        if pair == "(*":
            depth += 1
            out[i] = out[i + 1] = " "
            i += 2
            continue
        if depth and pair == "*)":
            depth -= 1
            out[i] = out[i + 1] = " "
            i += 2
            continue
        if depth and text[i] != "\n":
            out[i] = " "
        i += 1
    return "".join(out)


class Decl:
    def __init__(self, name: str, type_text: str, init: Optional[str], address: Optional[str]):
        self.name = name
        self.type_text = " ".join(type_text.split())
        self.init = init.strip() if init else None
        self.address = address

    def st(self) -> str:
        at = " AT {0}".format(self.address) if self.address else ""
        init = " := {0}".format(self.init) if self.init else ""
        return "{0}{1} : {2}{3};".format(self.name, at, self.type_text, init)


class VarList:
    def __init__(self, kind: str, constant: bool, decls: List[Decl]):
        self.kind = kind
        self.constant = constant
        self.decls = decls


class Pou:
    def __init__(self, kind: str, name: str, return_type: Optional[str],
                 blocks: List[VarList], body: str):
        self.kind = kind
        self.name = name
        self.return_type = return_type
        self.blocks = blocks
        self.body = body


class Source:
    """Everything export() needs, read out of the .st files."""

    def __init__(self) -> None:
        self.enums: List[Tuple[str, List[Tuple[str, str]]]] = []
        self.structs: List[Tuple[str, List[Decl]]] = []
        self.globals: List[Tuple[str, VarList]] = []
        self.pous: List[Pou] = []


def _decls(text: str) -> List[Decl]:
    result = []
    for line in text.split(";"):
        if not line.strip():
            continue
        match = _DECL.match(line + ";")
        if not match:
            raise ValueError("cannot read declaration {0!r}".format(line.strip()))
        result.append(Decl(match.group("name"), match.group("type"),
                           match.group("init"), match.group("addr")))
    return result


_VAR_BLOCK = re.compile(
    r"\b(?P<kind>VAR_GLOBAL|VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR_TEMP|VAR_EXTERNAL|VAR)\b"
    r"(?P<constant>\s+CONSTANT)?(?P<body>.*?)\bEND_VAR\b",
    re.S,
)
_POU = re.compile(
    r"\b(?P<kind>FUNCTION_BLOCK|FUNCTION|PROGRAM)\s+(?P<name>\w+)(?:\s*:\s*(?P<ret>\w+))?"
    r"(?P<inner>.*?)\bEND_(?P=kind)\b",
    re.S,
)
_TYPE_BLOCK = re.compile(r"\bTYPE\b(?P<body>.*?)\bEND_TYPE\b", re.S)
_ENUM = re.compile(r"(?P<name>\w+)\s*:\s*\((?P<members>[^)]*)\)\s*;", re.S)
_STRUCT = re.compile(r"(?P<name>\w+)\s*:\s*STRUCT\b(?P<body>.*?)\bEND_STRUCT\b\s*;?", re.S)


def read_source(plc_dir: str) -> Source:
    source = Source()
    paths = []
    for dirpath, _dirs, files in os.walk(plc_dir):
        paths.extend(os.path.join(dirpath, f) for f in files if f.lower().endswith(".st"))
    for path in sorted(paths):
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        clean = blank_comments(raw)
        stem = os.path.splitext(os.path.basename(path))[0]
        for block in _TYPE_BLOCK.finditer(clean):
            body = block.group("body")
            for match in _STRUCT.finditer(body):
                source.structs.append((match.group("name"), _decls(match.group("body"))))
            without_structs = _STRUCT.sub("", body)
            for match in _ENUM.finditer(without_structs):
                members = []
                for item in match.group("members").split(","):
                    name, _sep, value = item.partition(":=")
                    members.append((name.strip(), value.strip()))
                source.enums.append((match.group("name"), members))
        outside = _TYPE_BLOCK.sub(lambda m: " " * len(m.group(0)), clean)
        pous = list(_POU.finditer(outside))
        top = outside
        for match in pous:
            top = top.replace(match.group(0), " " * len(match.group(0)))
        for index, match in enumerate(_VAR_BLOCK.finditer(top)):
            if match.group("kind") != "VAR_GLOBAL":
                raise ValueError("{0}: {1} outside a POU".format(path, match.group("kind")))
            name = stem if index == 0 else "{0}_{1}".format(stem, index)
            source.globals.append(
                (name, VarList("VAR_GLOBAL", bool(match.group("constant")),
                               _decls(match.group("body"))))
            )
        for match in pous:
            inner_start = match.start("inner")
            inner = match.group("inner")
            blocks = []
            body_from = 0
            for block in _VAR_BLOCK.finditer(inner):
                blocks.append(VarList(block.group("kind"), bool(block.group("constant")),
                                      _decls(block.group("body"))))
                body_from = block.end()
            body = raw[inner_start + body_from : match.start(0) + len(match.group(0)) - len(
                "END_" + match.group("kind"))]
            source.pous.append(
                Pou(match.group("kind"), match.group("name"), match.group("ret"),
                    blocks, body.strip("\n"))
            )
    return source


def _check_against_parser(source: Source, plc_dir: str) -> None:
    from ..iec61131.runtime import Runtime

    runtime = Runtime()
    runtime.load_directory(plc_dir)
    parsed_globals = set()
    parsed_pous: Dict[str, int] = {}
    for unit in runtime._units:
        for block in unit.globals:
            parsed_globals.update(d.name.upper() for d in block.decls)
        for pou in unit.pous:
            parsed_pous[pou.name.upper()] = sum(len(b.decls) for b in pou.var_blocks)
    read_globals = {d.name.upper() for _n, vl in source.globals for d in vl.decls}
    if read_globals != parsed_globals:
        raise ValueError("global declarations disagree with the parser: {0}".format(
            sorted(read_globals ^ parsed_globals)))
    read_pous = {p.name.upper(): sum(len(b.decls) for b in p.blocks) for p in source.pous}
    if read_pous != parsed_pous:
        raise ValueError("POU declarations disagree with the parser")


# -- writing XML ------------------------------------------------------------------


def _q(tag: str) -> str:
    return "{%s}%s" % (NS, tag)


def _type(parent: ET.Element, type_text: str) -> None:
    text = type_text.strip()
    array = _ARRAY.match(text)
    if array:
        node = ET.SubElement(parent, _q("array"))
        for dim in array.group("dims").split(","):
            lower, _sep, upper = dim.partition("..")
            ET.SubElement(node, _q("dimension"), lower=lower.strip(), upper=upper.strip())
        _type(ET.SubElement(node, _q("baseType")), array.group("elem"))
        return
    string = _STRING.match(text)
    if string:
        attrs = {"length": string.group("len")} if string.group("len") else {}
        ET.SubElement(parent, _q("string" if string.group("kind").upper() == "STRING"
                                 else "wstring"), **attrs)
        return
    if text.upper() in ELEMENTARY:
        ET.SubElement(parent, _q(text.upper()))
        return
    ET.SubElement(parent, _q("derived"), name=text)


def _variable(parent: ET.Element, decl: Decl) -> None:
    attrs = {"name": decl.name}
    if decl.address:
        attrs["address"] = decl.address
    var = ET.SubElement(parent, _q("variable"), **attrs)
    _type(ET.SubElement(var, _q("type")), decl.type_text)
    if decl.init:
        init = ET.SubElement(var, _q("initialValue"))
        ET.SubElement(init, _q("simpleValue"), value=decl.init)


def _var_list(parent: ET.Element, tag: str, var_list: VarList, name: Optional[str] = None) -> None:
    attrs = {}
    if name:
        attrs["name"] = name
    if var_list.constant:
        attrs["constant"] = "true"
    node = ET.SubElement(parent, _q(tag), **attrs)
    for decl in var_list.decls:
        _variable(node, decl)


def _st_body(parent: ET.Element, text: str) -> None:
    st = ET.SubElement(parent, _q("ST"))
    para = ET.SubElement(st, "{%s}p" % XHTML)
    para.text = text


def export(plc_dir: str, program_order: Sequence[str], cycle: str = "T#200ms",
           creation: str = "1970-01-01T00:00:00", version: str = "1.0.0") -> str:
    source = read_source(plc_dir)
    _check_against_parser(source, plc_dir)
    ET.register_namespace("", NS)
    ET.register_namespace("xhtml", XHTML)
    project = ET.Element(_q("project"))
    ET.SubElement(project, _q("fileHeader"), companyName="CGainsWorks",
                  productName="plc-cip-batch-control", productVersion=version,
                  creationDateTime=creation,
                  contentDescription="CIP skid controller, exported from plc/")
    header = ET.SubElement(project, _q("contentHeader"), name="plc-cip-batch-control")
    coords = ET.SubElement(header, _q("coordinateInfo"))
    for language in ("fbd", "ld", "sfc"):
        ET.SubElement(ET.SubElement(coords, _q(language)), _q("scaling"), x="1", y="1")

    types = ET.SubElement(project, _q("types"))
    data_types = ET.SubElement(types, _q("dataTypes"))
    for name, members in source.enums:
        dt = ET.SubElement(data_types, _q("dataType"), name=name)
        enum = ET.SubElement(ET.SubElement(dt, _q("baseType")), _q("enum"))
        values = ET.SubElement(enum, _q("values"))
        for member, value in members:
            attrs = {"name": member}
            if value:
                attrs["value"] = value
            ET.SubElement(values, _q("value"), **attrs)
    for name, decls in source.structs:
        dt = ET.SubElement(data_types, _q("dataType"), name=name)
        struct = ET.SubElement(ET.SubElement(dt, _q("baseType")), _q("struct"))
        for decl in decls:
            _variable(struct, decl)

    pous = ET.SubElement(types, _q("pous"))
    for pou in source.pous:
        node = ET.SubElement(pous, _q("pou"), name=pou.name, pouType=POU_TYPES[pou.kind])
        interface = ET.SubElement(node, _q("interface"))
        if pou.return_type:
            _type(ET.SubElement(interface, _q("returnType")), pou.return_type)
        for block in pou.blocks:
            _var_list(interface, BLOCK_TAGS[block.kind], block)
        _st_body(ET.SubElement(node, _q("body")), pou.body)

    instances = ET.SubElement(project, _q("instances"))
    configs = ET.SubElement(instances, _q("configurations"))
    config = ET.SubElement(configs, _q("configuration"), name="CipSkid")
    resource = ET.SubElement(config, _q("resource"), name="CipController")
    task = ET.SubElement(resource, _q("task"), name="MainTask", interval=cycle, priority="1")
    for program in program_order:
        ET.SubElement(task, _q("pouInstance"), name=program + "_1", typeName=program)
    for name, var_list in source.globals:
        _var_list(config, "globalVars", var_list, name=name)

    ET.indent(project, space="  ")
    text = ET.tostring(project, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + text + "\n"


# -- reading XML back -------------------------------------------------------------


def _type_text(node: ET.Element) -> str:
    child = list(node)[0]
    tag = child.tag.split("}")[1]
    if tag == "array":
        dims = ", ".join("{0}..{1}".format(d.get("lower"), d.get("upper"))
                         for d in child.findall(_q("dimension")))
        return "ARRAY[{0}] OF {1}".format(dims, _type_text(child.find(_q("baseType"))))
    if tag in ("string", "wstring"):
        length = child.get("length")
        return tag.upper() + ("[{0}]".format(length) if length else "")
    if tag == "derived":
        return child.get("name")
    return tag


def _decl_lines(var_list: ET.Element, indent: str = "    ") -> List[str]:
    lines = []
    for var in var_list.findall(_q("variable")):
        init = var.find(_q("initialValue"))
        decl = Decl(var.get("name"), _type_text(var.find(_q("type"))),
                    init.find(_q("simpleValue")).get("value") if init is not None else None,
                    var.get("address"))
        lines.append(indent + decl.st())
    return lines


def _block_text(kind: str, var_list: ET.Element) -> List[str]:
    constant = " CONSTANT" if var_list.get("constant") == "true" else ""
    return [kind + constant] + _decl_lines(var_list) + ["END_VAR"]


def to_st(xml_text: str) -> Dict[str, str]:
    """The .st files a PLCopen export describes, keyed by file name."""
    root = ET.fromstring(xml_text)
    files: Dict[str, str] = {}
    types = root.find(_q("types"))
    lines = ["TYPE"]
    for dt in types.find(_q("dataTypes")).findall(_q("dataType")):
        base = list(dt.find(_q("baseType")))[0]
        kind = base.tag.split("}")[1]
        if kind == "enum":
            members = []
            for value in base.find(_q("values")).findall(_q("value")):
                text = value.get("name")
                if value.get("value") is not None:
                    text += " := " + value.get("value")
                members.append(text)
            lines.append("    {0} : ({1});".format(dt.get("name"), ", ".join(members)))
        elif kind == "struct":
            lines.append("    {0} : STRUCT".format(dt.get("name")))
            lines.extend(_decl_lines(base, "        "))
            lines.append("    END_STRUCT;")
        else:
            raise ValueError("unsupported data type {0}".format(kind))
    lines.append("END_TYPE")
    files["Types.st"] = "\n".join(lines) + "\n"

    for config in root.iter(_q("configuration")):
        for gvl in config.findall(_q("globalVars")):
            files[gvl.get("name") + ".st"] = "\n".join(_block_text("VAR_GLOBAL", gvl)) + "\n"

    for pou in types.find(_q("pous")).findall(_q("pou")):
        kind = POU_KINDS[pou.get("pouType")]
        interface = pou.find(_q("interface"))
        header = "{0} {1}".format(kind, pou.get("name"))
        ret = interface.find(_q("returnType"))
        if ret is not None:
            header += " : " + _type_text(ret)
        out = [header]
        for child in interface:
            tag = child.tag.split("}")[1]
            if tag in TAG_BLOCKS:
                out.extend(_block_text(TAG_BLOCKS[tag], child))
        body = pou.find(_q("body")).find(_q("ST")).find("{%s}p" % XHTML).text or ""
        out.append(body)
        out.append("END_" + kind)
        files[pou.get("name") + ".st"] = "\n".join(out) + "\n"
    return files
