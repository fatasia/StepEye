"""Six-face evidence aggregation, not a replacement for a battery defect model."""
from fastapi import APIRouter, Query

FACES = ('front','back','left','right','top','bottom')


def summarize_faces(records, product_id, sn, inspection_id):
    faces=[];seen={};duplicates=set()
    # A copied image cannot serve as independent evidence for two different faces.
    for record in records:
        face=record.get('face')
        if face not in FACES:continue
        for image in record['images']:
            digest=image['image_sha256']
            if digest in seen and seen[digest]!=face:duplicates.update((face,seen[digest]))
            seen[digest]=face
    for face in FACES:
        selected=[r for r in records if r.get('face')==face]
        # Idempotent retries return the existing record. Multiple distinct records
        # for a face need explicit review rather than silently selecting the latest.
        state=('missing' if not selected else 'conflict' if len(selected)>1 or face in duplicates
               else selected[0]['result'])
        faces.append({'face':face,'status':state,'records':selected})
    missing=[item['face'] for item in faces if item['status']=='missing']
    has_ng=any(record['result']=='NG' for record in records)
    state=('NG' if has_ng else 'review' if any(f['status'] in ('UNCERTAIN','conflict') for f in faces)
           else 'pending' if missing else 'complete')
    return {'product_id':product_id,'sn':sn,'inspection_id':inspection_id,'status':state,
            'missing_faces':missing,'duplicate_image_faces':sorted(duplicates),'faces':faces,
            'basis':'declared_equipment_results','model_acceptance':False}


def router(store):
    api=APIRouter()

    @api.get('/api/battery-inspections')
    def summary(product_id:str=Query(min_length=1,max_length=100),
                sn:str=Query(min_length=1,max_length=160),
                inspection_id:str=Query(min_length=1,max_length=100)):
        records=store.inspection_faces(product_id,sn,inspection_id)
        return summarize_faces(records,product_id,sn,inspection_id)
    return api
