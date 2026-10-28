# SPDX-License-Identifier: GPL-3.0-only
"""Original synthetic PDF fixtures; requires test-only reportlab, never customer drawings."""
import argparse
import json
from pathlib import Path


def generate(folder):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pdfencrypt import StandardEncryption
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)

    def drawing(name, pages, *, blank=False, encrypt=None, active=False, size=(842, 595)):
        c = canvas.Canvas(str(folder/name), pagesize=size, invariant=True, encrypt=encrypt)
        for page in range(pages):
            if not blank:
                c.setFont('Helvetica', 18)
                c.drawString(50, size[1]-45, f'WORKBENCH DEMO - SHEET {page+1} / {pages}')
                c.setStrokeColorRGB(.1, .25, .65 if page%2 == 0 else .2)
                c.setLineWidth(2)
                c.rect(48, 70, size[0]-96, size[1]-155)
                c.circle(size[0]/2, size[1]/2, 90)
                c.circle(size[0]/2, size[1]/2, 25)
                c.line(100, size[1]/2, size[0]-100, size[1]/2)
                c.line(size[0]/2, 100, size[0]/2, size[1]-100)
                c.setFont('Helvetica', 12)
                c.drawString(50, 40, 'SYNTHETIC / READ ONLY / NOT FOR MANUFACTURING')
                if active:
                    c.linkURL('https://invalid.example/never-open', (60, 450, 500, 500), relative=0)
                    c.drawString(60, 460, 'INERT LINK - MUST NOT OPEN')
            c.showPage()
        # JavaScript action is a deliberately inert security fixture, not an executable demo feature.
        if active:
            from reportlab.pdfbase.pdfdoc import PDFDictionary, PDFName, PDFString
            c._doc.Catalog.OpenAction = PDFDictionary({'S': PDFName('JavaScript'), 'JS': PDFString("app.launchURL('https://invalid.example/never-run');")})
        c.save()

    drawing('单页 工程图(演示).pdf', 1)
    drawing('多页 装配 😀.pdf', 4)
    drawing('空白页.pdf', 1, blank=True)
    drawing('链接与脚本.pdf', 1, active=True)
    drawing('加密.pdf', 1, encrypt=StandardEncryption('demo-only-password', ownerPassword='demo-only-owner', canPrint=0))
    drawing('超多页.pdf', 201, blank=True)
    drawing('页面超限.pdf', 1, size=(20000, 20000))
    (folder/'损坏.pdf').write_bytes(b'%PDF-1.7\ninvalid\n%%EOF')
    (folder/'空文件.pdf').write_bytes(b'')
    manifest = {f.name: f.stat().st_size for f in folder.iterdir()}
    (folder/'samples.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('output', type=Path)
    print(json.dumps(generate(parser.parse_args().output), ensure_ascii=True))
