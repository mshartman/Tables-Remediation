"""Keep minor vertical margin text without flattening the entire article page."""


def rotation_profile(page):
    lines=[line for block in page.get_text('dict',flags=0)['blocks'] for line in block.get('lines',[])]
    rotated=[line for line in lines if abs(line.get('dir',(1,0))[0]-1)>.01 or abs(line.get('dir',(1,0))[1])>.01]
    total=sum(len(s['text'].strip()) for line in lines for s in line['spans'])
    count=sum(len(s['text'].strip()) for line in rotated for s in line['spans'])
    def margin(line):
        x0,y0,x1,y1=line['bbox'];dx,dy=line.get('dir',(1,0))
        return abs(dx)<.1 and abs(abs(dy)-1)<.02 and (
            x1<=page.rect.width*.07 or x0>=page.rect.width*.93)
    minor=bool(rotated and total-count>=200 and count<=total*.15 and all(margin(line) for line in rotated))
    return {'requires_fallback':bool(rotated and not minor),
            'notes':[{'bbox':list(line['bbox']),'text':''.join(s['text'] for s in line['spans']).strip()}
                     for line in rotated] if minor else []}
