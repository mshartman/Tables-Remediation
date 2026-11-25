"""Carry simple column headers to an explicitly identified continuation page."""
from collections import defaultdict
import re
import unicodedata


_NUMBER = re.compile(r"^table\s+(\d+)(?![\w.]\d|\w)", re.I)
_CONTINUED = re.compile(r"(?:^|[\s|(,:;.-])continued\b[.)]?", re.I)
_PAGE = re.compile(r"^(?:layout-)?page-(\d+)$")


def _text(cell):
    return " ".join(unicodedata.normalize("NFKC", cell.get_text(" ", strip=True)).split())


def _caption_identity(table):
    caption = table.find("caption", recursive=False)
    if caption is None:
        return None
    value = _text(caption)
    match = _NUMBER.match(value)
    if not match:
        return None
    rest = value[match.end():].strip(" .:|–—-")
    # The marker must immediately follow the table number or terminate its title.
    marker = next((m for m in _CONTINUED.finditer(rest)
                   if not rest[:m.start()].strip(" .:|(-") or not rest[m.end():].strip(" .:|)-")), None)
    title = (rest[:marker.start()] + " " + rest[marker.end():]) if marker else rest
    return match[1], title.strip(" .:|(),–—-").casefold(), marker is not None


def _rows(table):
    return [row for body in table.find_all("tbody", recursive=False)
            for row in body.find_all("tr", recursive=False)]


def _cells(row):
    return row.find_all(["td", "th"], recursive=False)


def _simple_col_headers(table):
    head = table.find("thead", recursive=False)
    rows = head.find_all("tr", recursive=False) if head else []
    if len(rows) != 1:
        return []
    columns = _cells(rows[0])
    if len(columns) < 2 or not all(cell.name == "th" and cell.get("scope") == "col" and _text(cell)
                                   and str(cell.get("colspan", 1)) == str(cell.get("rowspan", 1)) == "1"
                                   for cell in columns):
        return []
    return columns


def _ordinary_rows(table, width):
    records = []
    for row in _rows(table):
        cells = _cells(row)
        if (len(cells) == 1 and cells[0].get("scope") == "rowgroup"
                and str(cells[0].get("colspan", 1)) == str(width)
                and str(cells[0].get("rowspan", 1)) == "1"):
            continue
        if len(cells) != width or any(str(c.get("colspan", 1)) != "1" or
                                      str(c.get("rowspan", 1)) != "1" for c in cells):
            return []
        records.append(cells)
    return records


def _assign_id(cell, base, ids):
    if cell.get("id"):
        return cell["id"]
    while base in ids:
        base += "-new"
    ids.add(base)
    cell["id"] = base
    return base


def _carry(soup, source, target, page_number, number, ids):
    if target.find("thead") or target.find("table") or source.find("table"):
        return
    columns = _simple_col_headers(source)
    if not columns:
        return
    width = len(columns)
    before = _ordinary_rows(source, width)
    after = _ordinary_rows(target, width)
    if not before or not after:
        return
    first = after[0]
    repeated = (target.get("data-local-table") != "tagged"
                and first[0].parent is _rows(target)[0]
                and [_text(c).casefold() for c in first] == [_text(c).casefold() for c in columns])
    if repeated and len(after) == 1:
        return

    head = soup.new_tag("thead")
    target.find("tbody", recursive=False).insert_before(head)
    if repeated:
        head.append(first[0].parent.extract())  # retain links, markup and existing IDs
        labels = first
        after = after[1:]
    else:
        header_row = soup.new_tag("tr")
        head.append(header_row)
        labels = []
        for col in columns:
            label = soup.new_tag("th")
            label.string = _text(col)
            header_row.append(label)
            labels.append(label)
    prefix = f"continued-p{page_number}-t{number}"
    for c, label in enumerate(labels):
        label.name = "th"
        label["scope"] = "col"
        label.attrs.pop("headers", None)
        _assign_id(label, f"{prefix}-col{c+1}", ids)
    # Continue a consistently used row-label role, unless authored tags specify data cells.
    row_labels = (target.get("data-local-table") != "tagged" and
                  all(cells[0].get("scope") == "row" for cells in before))
    for r, cells in enumerate(after):
        first = cells[0]
        if row_labels and _text(first):
            first.name = "th"
            first["scope"] = "row"
        row_id = _assign_id(first, f"{prefix}-row{r+1}", ids) if first.get("scope") == "row" else None
        for c, cell in enumerate(cells):
            refs = [labels[c]["id"]]
            if c and row_id:
                refs.append(row_id)
            existing = cell.get("headers", [])
            if isinstance(existing, str):
                existing = existing.split()
            cell["headers"] = list(dict.fromkeys([*refs, *existing]))
    target.attrs.pop("data-local-header-status", None)
    target["data-reader-headers-repaired"] = ""
    target["data-reader-continued-headers"] = "repeated" if repeated else "carried"


def carry_continuation_headers(soup):
    """Use adjacent page numbers, explicit matching captions, and matching row widths.

    Leave mismatched titles, spans, and ambiguous table numbers unchanged. Header IDs
    are always local to their table so assistive software can resolve them in isolation.
    """
    ids = {node["id"] for node in soup.select("[id]")}
    previous, previous_number = {}, None
    for page in soup.select(".page"):
        match = _PAGE.fullmatch(page.get("id", ""))
        page_number = int(match[1]) if match else None
        current = defaultdict(list)
        if page_number is None:
            marker = page.select_one('hr[role=doc-pagebreak][id]')
            match = _PAGE.fullmatch(marker['id']) if marker else None
            page_number = int(match[1]) if match else None
        for table in page.find_all("table"):
            if table.find_parent("table"):
                continue
            info = _caption_identity(table)
            if info:
                number, title, continued = info
                current[number].append((table, title, continued))
        if page_number is not None and previous_number == page_number - 1:
            for number, items in current.items():
                prior = previous.get(number, ())
                if len(items) != 1 or len(prior) != 1:
                    continue
                target, title, continued = items[0]
                source, source_title, _ = prior[0]
                if continued and (not title or not source_title or title == source_title):
                    _carry(soup, source, target, page_number, number, ids)
        previous, previous_number = current, page_number
