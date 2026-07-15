"""Fast structural validation for very large CSV/XLSX batches.

The general spreadsheet inspector loads all cells and is intentionally avoided
after it times out on a multi-million-cell workbook.  This validator reads only
worksheet metadata plus the first two XML rows.
"""

import argparse
import csv
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def workbook_sheet_targets(archive):
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
    }
    result = {}
    for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
        target = targets[sheet.attrib[f"{{{REL_NS}}}id"]].lstrip("/")
        result[sheet.attrib["name"]] = target if target.startswith("xl/") else f"xl/{target}"
    return result


def inspect_sheet(archive, target):
    dimension = None
    cells = {}
    with archive.open(target) as stream:
        for event, element in ET.iterparse(stream, events=("end",)):
            if element.tag == f"{{{MAIN_NS}}}dimension":
                dimension = element.attrib.get("ref")
            elif element.tag == f"{{{MAIN_NS}}}c":
                address = element.attrib.get("r", "")
                if address in {"A1", "C1", "SG1", "SH1", "A2", "C2", "SG2", "SH2"}:
                    text = element.find(f".//{{{MAIN_NS}}}t")
                    value = element.find(f"{{{MAIN_NS}}}v")
                    cells[address] = text.text if text is not None else (value.text if value is not None else None)
                element.clear()
            elif element.tag == f"{{{MAIN_NS}}}row" and int(element.attrib.get("r", "0")) >= 2:
                break
    return {"dimension": dimension, "cells": cells}


def inspect_xlsx(path):
    with zipfile.ZipFile(path) as archive:
        targets = workbook_sheet_targets(archive)
        return {name: inspect_sheet(archive, target) for name, target in targets.items()}


def inspect_csv(path):
    header = None
    data_rows = 0
    first_row = None
    last_row = None
    with Path(path).open("r", newline="", encoding="utf-8-sig") as stream:
        for row in csv.reader(stream):
            if header is None:
                if row and row[0] == "Pixel":
                    header = row
                continue
            if row:
                data_rows += 1
                first_row = first_row or row
                last_row = row
    return {
        "columns": len(header or []),
        "data_rows": data_rows,
        "first_frame_header": header[2] if header and len(header) > 2 else None,
        "last_frame_header": header[-1] if header else None,
        "first_pixel_first_frame": first_row[2] if first_row else None,
        "first_pixel_last_frame": first_row[-1] if first_row else None,
        "last_pixel_index": last_row[0] if last_row else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--xlsx", required=True)
    parser.add_argument("--csv", nargs="+", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = {
        "xlsx": inspect_xlsx(args.xlsx),
        "csv": {Path(path).stem.rsplit("_", 1)[-1]: inspect_csv(path) for path in args.csv},
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
