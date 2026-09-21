"""Check the final manuscript PDF, figure atlas, and guarded source access."""
import json
import re
import hashlib
from pathlib import Path
import pymupdf
from PIL import Image,ImageDraw
import data

def main():
    root=data.ROOT;paper=root/'paper';qa=Path('/tmp/pt60-final-paper');qa.mkdir(exist_ok=True)
    pdf=pymupdf.open(paper/'PT60_Sep16.pdf');captions=[];tables=[];outside=[];sparse=[]
    expected={1:'Same-voltage',2:'Bus voltage',3:'Daily interval',4:'Field-specific',5:'Jaccard',6:'Paired intraday',7:'Substation-season',8:'residual distributions',9:'Voltage extrema',10:'Loading response',11:'Screening criteria'}
    md=(paper/'PT60_Sep16.MD').read_text()
    endings={int(n):caption[-12:] for n,caption in re.findall(r'^\*\*Figure (\d+)[^\n]*?\*\*([^\n]*)',md,re.M)}
    for i,page in enumerate(pdf):
        text=page.get_text();nums=[int(x) for x in re.findall(r'Figure (\d+)——',text)]
        for n in nums:
            assert expected[n] in text,(i+1,n,'Caption and figure not together')
            assert re.sub(r'\s','',endings[n]) in re.sub(r'\s','',text),(i+1,n,'Caption split across pages')
        captions.extend(nums);tables.extend(re.findall(r'Table (S?\d+)——',text))
        for b in page.get_text('dict')['blocks']:
            for line in b.get('lines',[]):
                for s in line['spans']:
                    if not (page.rect+(-1,-1,1,1)).contains(pymupdf.Rect(s['bbox'])):outside.append([i+1,s['text']])
        # Detect accidental pages containing only a header/footer and a stray intro.
        body=[w for w in page.get_text('words') if 70<w[1]<page.rect.height-50]
        if body and max(w[3] for w in body)-min(w[1] for w in body)<100:sparse.append(i+1)
        page.get_pixmap(matrix=pymupdf.Matrix(1.25,1.25)).save(qa/f'page-{i+1:02d}.png')
    assert captions==list(range(1,12)),captions
    assert tables==[str(x) for x in range(1,16)]+['S1','S2','S3','S4'],tables
    assert not outside,outside
    assert not sparse,sparse
    atlas=pymupdf.open(paper/'PT60_Sep16_FIGURES.pdf');assert len(atlas)==11
    for i,page in enumerate(atlas):assert expected[i+1] in page.get_text()
    # Prove a changed source is rejected without modifying any on-disk source.
    rel=next(iter(data.LOCK));digest=data.LOCK[rel];data.LOCK[rel]='0'*64
    try:
        try:data.source(rel)
        except RuntimeError as e:assert 'VERSION / SNAPSHOT CONFLICT' in str(e)
        else:raise AssertionError('Source drift guard did not reject changed digest')
    finally:data.LOCK[rel]=digest
    for start in range(0,len(pdf),6):
        canvas=Image.new('RGB',(1500,1460),'#ddd')
        for k in range(min(6,len(pdf)-start)):
            im=Image.open(qa/f'page-{start+k+1:02d}.png');im.thumbnail((485,680))
            x=k%3*500;y=k//3*730;canvas.paste(im,(x+(500-im.width)//2,y+28))
            ImageDraw.Draw(canvas).text((x+10,y+7),f'Page {start+k+1}',fill='black')
        canvas.save(qa/f'contact-{start//6}.jpg')
    report={'pdf_pages':len(pdf),'figure_captions':captions,'table_captions':tables,
            'caption_and_figure_same_page':True,'out_of_bounds_text':outside,'sparse_pages':sparse,
            'atlas_pages':len(atlas),'source_drift_guard':'passed; no files modified',
            'pdf_sha256':hashlib.sha256((paper/'PT60_Sep16.pdf').read_bytes()).hexdigest(),
            'visual_review_directory':str(qa)}
    (data.OUT/'delivery_validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
