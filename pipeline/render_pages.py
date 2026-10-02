"""Helper for reviewing plan PDFs: render a contact sheet of pages, or single pages at high resolution.
usage: render_pages.py sheet <pdf> <first> <last> <out.png>
       render_pages.py page  <pdf> <page> <out.png> [scale]
"""
import sys
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

mode, pdf = sys.argv[1], sys.argv[2]
doc = pdfium.PdfDocument(pdf)
if mode == "sheet":
    first, last, out = int(sys.argv[3]), min(int(sys.argv[4]), len(doc)), sys.argv[5]
    thumbs = []
    for i in range(first - 1, last):
        pg = doc[i]
        w = pg.get_width()
        img = pg.render(scale=230 / w).to_pil().convert("RGB")
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 40, 18], fill="yellow")
        d.text((3, 3), str(i + 1), fill="black")
        thumbs.append(img)
    cols = 8
    cw, ch = 235, max(t.height for t in thumbs) + 5
    sheet = Image.new("RGB", (cols * cw, ((len(thumbs) + cols - 1) // cols) * ch), "white")
    for k, t in enumerate(thumbs):
        sheet.paste(t, ((k % cols) * cw, (k // cols) * ch))
    sheet.save(out)
else:
    p, out = int(sys.argv[3]), sys.argv[4]
    scale = float(sys.argv[5]) if len(sys.argv) > 5 else 2.2
    doc[p - 1].render(scale=scale).to_pil().convert("RGB").save(out)
