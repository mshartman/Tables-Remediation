"""Generate a public, synthetic regression fixture; no real article content."""
from pathlib import Path


def generate(path=None):
    from reportlab.pdfgen.canvas import Canvas
    target = Path(path) if path else Path(__file__).with_name('untagged-headerless.pdf')
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(target), pagesize=(420, 300), invariant=1, pdfVersion=(1, 4))
    canvas.setTitle('Synthetic untagged headerless table')
    canvas.setAuthor('Synthetic test fixture')
    canvas.setSubject('Three data rows. No heading row or PDF structure tags.')
    canvas.setLineWidth(.6)
    for x in (60, 180, 300):
        canvas.line(x, 90, x, 210)
    for y in (90, 130, 170, 210):
        canvas.line(60, y, 300, y)
    canvas.setFont('Helvetica', 12)
    for y, row in zip((185, 145, 105), (('2022', '12'), ('2023', '15'), ('2024', '19'))):
        for x, value in zip((72, 192), row):
            canvas.drawString(x, y, value)
    canvas.showPage()
    canvas.save()
    return target


if __name__ == '__main__':
    print(generate())
