"""Multi-example matching. All analysis uses saved embeddings; originals stay untouched."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','2')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS','2')
import hashlib,json,sqlite3,time
from pathlib import Path
import numpy as np
import engine,regroup
VERSION=1

def catalog(c):
 rows=c.execute('SELECT id,person,photo,embedding,thumb FROM faces ORDER BY person,id').fetchall()
 grouped={}
 for r in rows:grouped.setdefault(r['person'],[]).append(r)
 names=dict(c.execute('SELECT id,name FROM people'))
 return grouped,names

def evidence(c):
 """Second-best example support prevents a single similar face driving a merge."""
 grouped,names=catalog(c);ids=list(grouped)
 key=hashlib.sha256((str(VERSION)+regroup.fingerprint(c)).encode())
 for group in grouped.values():
  for r in group:key.update(r['embedding'])
 key=key.hexdigest();path=engine.DATA/'match-evidence.npz'
 if path.exists():
  try:
   with np.load(path,allow_pickle=False) as z:
    if str(z['key'])==key:return grouped,names,ids,z['scores'].copy(),z['floors'].copy()
  except (ValueError,OSError,KeyError):pass
 samples=[]
 for pid in ids:
  # One example per source photo, even if a previous manual merge combined two faces.
  unique={}
  for r in grouped[pid]:unique.setdefault(r['photo'],r)
  v=np.stack([np.frombuffer(r['embedding'],np.float32) for r in unique.values()])
  v=v/np.maximum(np.linalg.norm(v,axis=1,keepdims=True),1e-8);samples.append(v)
 scores=np.full((len(ids),len(ids)),-1,np.float32);floors=scores.copy()
 if ids:
  offsets=np.cumsum([0]+[len(v) for v in samples]);allv=np.concatenate(samples)
  for i,v in enumerate(samples):
   query=regroup.representatives(v,32);sim=query@allv.T
   for j in range(len(ids)):
    if i==j or len(samples[j])<3:continue
    second=np.partition(sim[:,offsets[j]:offsets[j+1]],-2,axis=1)[:,-2]
    scores[i,j]=np.quantile(second,0 if len(query)<=4 else .2)
    floors[i,j]=second.min()
 tmp=engine.DATA/f'match-evidence.{os.getpid()}.npz'
 np.savez_compressed(tmp,key=key,scores=scores,floors=floors);tmp.replace(path)
 return grouped,names,ids,scores,floors

def candidates(c):
 grouped,names,ids,scores,floors=evidence(c)
 photos={p:{r['photo'] for r in group} for p,group in grouped.items()}
 blocked=regroup.review_pairs(c)
 protected={r[0] for r in c.execute('SELECT person FROM regroup_protected')} if c.execute("SELECT 1 FROM sqlite_master WHERE name='regroup_protected'").fetchone() else set()
 out=[]
 for i,src in enumerate(ids):
  # Existing, larger groups provide the reference. Keep named identities stable.
  if names[src] or src in protected or len(ids)<2:continue
  order=np.argsort(scores[i])[::-1];j=int(order[0]);dst=ids[j]
  score=float(scores[i,j]);margin=score-float(scores[i,int(order[1])])
  if score<.48 or margin<.04 or len(photos[dst])<5 or len(photos[src])>=len(photos[dst]):continue
  if dst in protected or tuple(sorted((src,dst))) in blocked or photos[src]&photos[dst]:continue
  out.append({'source':src,'keep':dst,'score':score,'margin':margin,'floor':float(floors[i,j]),
              'automatic':score>=.62 and margin>=.10 and floors[i,j]>=.50 and len(grouped[src])<=20})
 return grouped,names,sorted(out,key=lambda x:(x['score'],x['margin']),reverse=True)

def plan(c):
 before=regroup.fingerprint(c)
 grouped,names,edges=candidates(c)
 groups={p:{'photos':{r['photo'] for r in rows},'members':{p}} for p,rows in grouped.items()}
 blocked=regroup.review_pairs(c);parent={p:p for p in groups};merges=[]
 def resolve(p):
  while parent[p]!=p:p=parent[p]
  return p
 for edge in edges:
  if not edge['automatic']:continue
  src,dst=resolve(edge['source']),resolve(edge['keep'])
  # Never promote a newly merged source into an automatic reference in this pass.
  if src!=edge['source'] or dst!=edge['keep'] or src==dst:continue
  a,b=groups[src],groups[dst]
  if len(a['members'])>1:continue
  if a['photos']&b['photos'] or any(tuple(sorted((x,y))) in blocked for x in a['members'] for y in b['members']):continue
  b['photos'].update(a['photos']);b['members'].update(a['members']);parent[src]=dst;del groups[src]
  merges.append(edge)
 result={'version':2,'method':'multiple-examples','fingerprint':before,'before':len(grouped),'after':len(groups),
         'faces':sum(map(len,grouped.values())),'mapping':{str(p):dst for dst,g in groups.items() for p in g['members']},'merges':merges}
 (engine.DATA/'regroup-plan.json').write_text(json.dumps(result))
 return result

def backup(c,label):
 folder=engine.DATA/'backups';folder.mkdir(exist_ok=True)
 path=folder/f'{label}-{time.time_ns()}.sqlite'
 with sqlite3.connect(path) as target:c.backup(target)
 return path

def improve(c):
 result=plan(c)
 if not result['merges']:return {'message':'No additional strong matches found. Use batch review for uncertain matches.'}
 path=backup(c,'before-smart-regroup')
 regroup.apply(c)
 return {'message':f"Combined {len(result['merges']):,} duplicate groups. {result['after']:,} groups remain, including uncertain small groups. Library → Undo automatic regrouping is available until the next edit.",'backup':str(path)}

def batch_summary(c):
 grouped,names,edges=candidates(c);by_target={}
 for e in edges:by_target.setdefault(e['keep'],[]).append(e)
 batches=[]
 for dst,items in by_target.items():
  # Diverse views make the comparison more useful than three nearly identical crops.
  rows=grouped[dst];v=np.stack([np.frombuffer(r['embedding'],np.float32) for r in rows]);center=v.mean(axis=0);center/=max(np.linalg.norm(center),1e-8)
  chosen=[int(np.argmax(v@center))];coverage=v@v[chosen[0]]
  for _ in range(min(3,len(rows))-1):
   idx=int(np.argmin(coverage));chosen.append(idx);coverage=np.maximum(coverage,v@v[idx])
  batches.append({'id':dst,'name':names[dst] or f'Person {dst}','count':len({r['photo'] for r in rows}),
    'thumbs':[rows[i]['thumb'] for i in chosen],
    'items':[{'id':e['source'],'count':len({r['photo'] for r in grouped[e['source']]}),'thumbs':[r['thumb'] for r in grouped[e['source']][:3]]} for e in items]})
 batches.sort(key=lambda b:(len(b['items']),sum(x['count'] for x in b['items'])),reverse=True)
 return {'batches':batches,'remaining':len(edges),'people':len(batches)}

def merge_batch(c,dst,sources):
 sources=list(dict.fromkeys(sources))
 if not sources or dst in sources:raise ValueError('Select one or more other groups to merge.')
 # A full snapshot is retained for recovery even after subsequent manual work.
 path=backup(c,'before-batch-merge')
 c.execute('BEGIN IMMEDIATE')
 try:
  grouped,names=catalog(c)
  if dst not in grouped or any(p not in grouped for p in sources):raise ValueError('Groups changed. Refresh batch review.')
  blocked=regroup.review_pairs(c,only_different=True);members=[dst]+sources;seen=set()
  for p in members:
   photos={r['photo'] for r in grouped[p]}
   if seen&photos:raise ValueError('Some selected groups appear together in a photo. Deselect them and review separately.')
   seen.update(photos)
  if any(tuple(sorted((a,b))) in blocked for i,a in enumerate(members) for b in members[i+1:]):raise ValueError('Some selected groups were marked as different people.')
  if any(names[p] for p in sources):raise ValueError('Named groups must be merged individually.')
  for src in sources:
   c.execute('UPDATE faces SET person=? WHERE person=?',(dst,src));c.execute('DELETE FROM people WHERE id=?',(src,))
  c.commit()
 except:c.rollback();raise
 return {'message':f'Merged {len(sources)} selected groups.','backup':str(path)}
