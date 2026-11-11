"""Recover simple ruled tables with explicit headers and sequential row IDs.

Uncertain column/row assignments are reported, never substituted for source text.
"""
from html import escape
import re
import unicodedata


def plain(words):
    return ' '.join(unicodedata.normalize('NFKC',w[4]) for w in sorted(words,key=lambda w:(round(w[1]/2),w[0])))


def header_groups(words):
    # Join neighboring words on each header line before clustering stacked labels.
    lines=[]
    for word in sorted(words,key=lambda w:(round(w[1]/2),w[0])):
        line=next((line for line in lines if abs(line[0][1]-word[1])<2),None)
        if line is None:lines.append([word])
        else:line.append(word)
    phrases=[]
    for line in lines:
        for word in sorted(line,key=lambda w:w[0]):
            if phrases and abs(phrases[-1][-1][1]-word[1])<2 and word[0]-phrases[-1][-1][2]<5:
                phrases[-1].append(word)
            else:phrases.append([word])
    groups=[]
    for phrase in phrases:
        left=min(w[0] for w in phrase);right=max(w[2] for w in phrase)
        hits=[g for g in groups if min(right,g['right'])>max(left,g['left'])]
        if len(hits)>1:raise ValueError('Header spans several possible columns.')
        if hits:
            g=hits[0];g['left']=min(left,g['left']);g['right']=max(right,g['right']);g['words']+=phrase
        else:groups.append({'left':left,'right':right,'words':list(phrase)})
    return sorted(groups,key=lambda g:g['left'])


def ruled_tables(page):
    words=page.get_text('words')
    captions=[]
    for block in page.get_text('dict')['blocks']:
        if not block.get('lines'):continue
        text=' '.join(''.join(s['text'] for s in line['spans']) for line in block['lines'])
        if re.match(r'^Table\s+\d+[.:]',text,re.I):captions.append((block['bbox'],text))
    rules=[]
    for drawing in page.get_drawings():
        box=drawing['rect']
        if box.width>page.rect.width*.5 and box.height<2:
            rules.append((box.x0,(box.y0+box.y1)/2,box.x1))
    rules=sorted(set(rules),key=lambda r:r[1])
    accepted=[];findings=[]
    for caption_box,caption in captions:
        try:
            candidates=[r for r in rules if r[1]>=caption_box[3]-1 and abs(r[0]-caption_box[0])<12]
            if len(candidates)<3 or candidates[0][1]-caption_box[3]>18:
                raise ValueError('No unambiguous header/body ruling was found.')
            top,separator,bottom=candidates[:3]
            if not top[1]<separator[1]<bottom[1] or bottom[1]-separator[1]<40:
                raise ValueError('Table boundary is ambiguous.')
            if any(info['bbox'][0]<top[2] and info['bbox'][2]>top[0]
                   and info['bbox'][1]<bottom[1] and info['bbox'][3]>top[1] for info in page.get_image_info()):
                raise ValueError('An image overlaps the table; cell content needs review.')
            header=[w for w in words if w[1]>top[1]-1 and w[3]<separator[1]+1 and top[0]-1<=w[0]<=top[2]]
            groups=header_groups(header)
            if not 2<=len(groups)<=12:raise ValueError('Column headers could not be separated.')
            starts=[g['left'] for g in groups]
            bounds=[top[0]-1]+[x-3 for x in starts[1:]]+[top[2]+1]
            body=[w for w in words if w[1]>=separator[1]-1 and w[3]<=bottom[1]+1 and top[0]-1<=w[0]<=top[2]]
            intersecting=[w for w in words if w[0]<top[2] and w[2]>top[0] and w[1]<bottom[1] and w[3]>separator[1]]
            if len(body)!=len(intersecting):raise ValueError('Text crosses the table boundary.')
            anchors=sorted([w for w in body if bounds[0]<=w[0]<bounds[1] and re.fullmatch(r'\d+',w[4])],key=lambda w:w[1])
            numbers=[int(w[4]) for w in anchors]
            if len(anchors)<3 or numbers!=list(range(numbers[0],numbers[0]+len(numbers))):
                raise ValueError('Sequential row identifiers are required for this local strategy.')
            rows=[[[] for _ in groups] for _ in anchors]
            for word in body:
                cols=[i for i in range(len(groups)) if bounds[i]<=word[0]<bounds[i+1]]
                row=[i for i,a in enumerate(anchors) if word[1]>=a[1]-2]
                if len(cols)!=1 or not row or word[2]>bounds[cols[0]+1]+1:
                    raise ValueError('A word crosses a column or row boundary.')
                rows[row[-1]][cols[0]].append(word)
            if any(not cell for row in rows for cell in row):raise ValueError('Some detected cells are empty or ambiguous.')
            labels=[plain(g['words']) for g in groups]
            values=[[plain(cell) for cell in row] for row in rows]
            if len({id(w) for row in rows for cell in row for w in cell})!=len(body):
                raise ValueError('Source words were not assigned exactly once.')
            html='<table data-local-table="ruled"><caption>'+escape(caption)+'</caption><thead><tr>'
            html+=''.join('<th scope="col">'+escape(label)+'</th>' for label in labels)+'</tr></thead><tbody>'
            for row in values:
                html+='<tr><th scope="row">'+escape(row[0])+'</th>'+''.join('<td>'+escape(cell)+'</td>' for cell in row[1:])+'</tr>'
            html+='</tbody></table>'
            box=(top[0]-1,caption_box[1]-1,top[2]+1,bottom[1]+1)
            accepted.append({'bbox':box,'html':html})
            findings.append({'status':'structured_needs_review','caption':caption,'rows':len(values),'columns':len(groups),
                             'assigned_words':len(body),'bbox':list(box)})
        except ValueError as exc:
            findings.append({'status':'needs_review','caption':caption,'reason':str(exc)})
    return accepted,findings


def inside(line,box):
    b=line['bbox'];x=(b[0]+b[2])/2;y=(b[1]+b[3])/2
    return box[0]<=x<=box[2] and box[1]<=y<=box[3]
