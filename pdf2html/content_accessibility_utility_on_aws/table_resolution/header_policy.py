"""Conservative evidence for inferring one untagged column-header row."""
import re

# Unknown labels can be reviewed; ordinary names, measurements, and record
# identifiers should never become column names merely because they appear first.
FIELD_NAMES = set("""id identifier name description value unit units year date group type category
    number count code gene protein substrate substrates transport expression modality disease diseases
    compound parameter method reference references sample measurement condition concentration
    temperature pressure quantity amount result results frequency percentage""".split())


def plausible_labels(labels):
    if len(labels) < 2:
        return False
    tokens = [re.findall(r"[a-z]+", label.casefold()) for label in labels]
    if any(not words or re.match(r"^\s*[+-]?\d", label) for words, label in zip(tokens, labels)):
        return False
    return sum(bool(FIELD_NAMES.intersection(words)) for words in tokens) >= max(2, (len(tokens)+1)//2)


def established_column_headers(labels, first_bold_fraction, body_bold_fraction):
    return (plausible_labels(labels) and first_bold_fraction >= .8
            and body_bold_fraction <= .4)


def inferred_header_rows(cells, values, groups):
    top = [c for c in cells if c['row'] == 0]
    if any(c['row_span'] != 1 or c['column_span'] != 1 for c in top):
        return 0
    def weight(cells):
        chars = [ch for c in cells for ch in groups[c['id']] if ch['text'].strip()]
        if not chars:
            return 0
        return sum('bold' in ch['fontname'].lower() for ch in chars) / len(chars)
    body = [c for c in cells if c['row'] and c['column_span'] == 1]
    return int(bool(body) and established_column_headers(
        [values[c['id']] for c in top], weight(top), weight(body)))
