"""Reversible collection-wide merging of saved face groups; no source photos read."""
import os
os.environ.setdefault('VECLIB_MAXIMUM_THREADS','2')
os.environ.setdefault('OPENBLAS_NUM_THREADS','2')
import hashlib,json,sqlite3,time,sys
from pathlib import Path
import numpy as np
import engine

def fingerprint(c):
 h=hashlib.sha256()
 for row in c.execute('SELECT id,photo,person FROM faces ORDER BY id'):h.update(str(tuple(row)).encode())
 for row in c.execute('SELECT id,name FROM people ORDER BY id'):h.update(str(tuple(row)).encode())
 for row in c.execute('SELECT face_a,face_b,decision FROM review_decisions ORDER BY id'):h.update(str(tuple(row)).encode())
 if c.execute("SELECT 1 FROM sqlite_master WHERE name='regroup_protected'").fetchone():
  for row in c.execute('SELECT person FROM regroup_protected ORDER BY person'):h.update(str(tuple(row)).encode())
 return h.hexdigest()

def representatives(v,limit=8):
 if len(v)<=limit:return v.copy()
 center=v.mean(axis=0);center/=max(np.linalg.norm(center),1e-8)
 chosen=[int(np.argmax(v@center))];coverage=v@v[chosen[0]]
 for _ in range(limit-1):
  idx=int(np.argmin(coverage));chosen.append(idx);coverage=np.maximum(coverage,v@v[idx])
 return v[chosen]

def can_merge(a,b,stage):
 if a['photos'] & b['photos']:return False
 if set(a.get('members',[])) & b.get('forbidden',set()) or set(b.get('members',[])) & a.get('forbidden',set()):return False
 if a['protected'] or b['protected']:return False
 if a['named'] and b['named']:return False
 cosine=float(a['center']@b['center'])
 if cosine < (0.68 if stage==0 else 0.60):return False
 sim=a['reps']@b['reps'].T
 if stage==0:return float(sim.max())>=0.72
 # The relaxed pass requires corroboration from multiple examples on BOTH sides.
 return len(a['reps'])>=2 and len(b['reps'])>=2 and float(sim.max())>=0.74 and int((sim.max(axis=1)>=0.66).sum())>=2 and int((sim.max(axis=0)>=0.66).sum())>=2

def plan(c):
 started=time.monotonic();before=fingerprint(c)
 rows=c.execute('SELECT id,photo,person,embedding FROM faces ORDER BY id').fetchall()
 v=np.stack([np.frombuffer(r['embedding'],np.float32) for r in rows]);v/=np.maximum(np.linalg.norm(v,axis=1,keepdims=True),1e-8)
 people=np.array([r['person'] for r in rows]);photos=np.array([r['photo'] for r in rows]);names={r['id']:r['name'] for r in c.execute('SELECT id,name FROM people')}
 c.execute('CREATE TABLE IF NOT EXISTS regroup_protected(person INTEGER PRIMARY KEY)');c.commit()
 protected={r[0] for r in c.execute('SELECT person FROM regroup_protected')}
 groups={}
 for pid in np.unique(people):
  idx=np.flatnonzero(people==pid);s=v[idx].sum(axis=0)
  groups[int(pid)]={'sum':s,'center':s/np.linalg.norm(s),'reps':representatives(v[idx]),'photos':set(photos[idx].tolist()),'members':[int(pid)],'named':bool(names[int(pid)]),'protected':int(pid) in protected,'size':len(idx)}
 for left,right in review_pairs(c,only_different=True):
  if left in groups and right in groups:
   groups[left].setdefault('forbidden',set()).add(right);groups[right].setdefault('forbidden',set()).add(left)
 original=len(groups);merges=[]
 for stage in [0,1]:
  for iteration in range(10):
   ids=list(groups);centers=np.stack([groups[i]['center'] for i in ids]);edges=[]
   for start in range(0,len(ids),256):
    sim=centers[start:start+256]@centers.T
    for local,row in enumerate(sim):
     i=start+local;row[:i+1]=-2;k=min(16,len(ids));near=np.argpartition(row,-k)[-k:]
     for j in near:
      if row[j]>=(0.68 if stage==0 else 0.60):edges.append((float(row[j]),ids[i],ids[j]))
   edges.sort(reverse=True);parent={i:i for i in ids};n=0
   def resolve(x):
    while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
    return x
   for score,ai,bi in edges:
    ai,bi=resolve(ai),resolve(bi)
    if ai==bi:continue
    a,b=groups[ai],groups[bi]
    if not can_merge(a,b,stage):continue
    # Keep named group identifiers stable, otherwise prefer the larger group.
    if b['named'] or (not a['named'] and b['size']>a['size']):ai,bi=bi,ai;a,b=b,a
    merges.append({'keep':ai,'from':bi,'cosine':round(float(a['center']@b['center']),4),'stage':stage})
    a['sum']+=b['sum'];a['center']=a['sum']/np.linalg.norm(a['sum']);a['photos'].update(b['photos']);a['members']+=b['members'];a['size']+=b['size'];a['reps']=representatives(np.concatenate([a['reps'],b['reps']]))
    a.setdefault('forbidden',set()).update(b.get('forbidden',set()));parent[bi]=ai;del groups[bi];n+=1
   print(f'Stage {stage+1}, pass {iteration+1}: {n} merges, {len(groups)} groups',flush=True)
   if not n:break
 result={'version':1,'fingerprint':before,'before':original,'after':len(groups),'faces':len(rows),'seconds':round(time.monotonic()-started,1),'mapping':{str(member):gid for gid,g in groups.items() for member in g['members']},'merges':merges,'remaining_single_faces':sum(g['size']==1 for g in groups.values()),'named_groups':{names[i]:{'id':i,'faces':g['size']} for i,g in groups.items() if g['named']}}
 (engine.DATA/'regroup-plan.json').write_text(json.dumps(result))
 print(json.dumps({k:val for k,val in result.items() if k not in ['mapping','merges']}),flush=True)
 return result

def apply(c):
 plan=json.loads((engine.DATA/'regroup-plan.json').read_text());c.execute('BEGIN IMMEDIATE')
 try:
  if fingerprint(c)!=plan['fingerprint']:raise ValueError('Catalog changed after planning. Generate a fresh regrouping plan.')
  c.execute('CREATE TABLE IF NOT EXISTS regroup_undo_faces(id INTEGER PRIMARY KEY,person INTEGER)')
  c.execute('CREATE TABLE IF NOT EXISTS regroup_undo_people(id INTEGER PRIMARY KEY,name TEXT)')
  c.execute('DELETE FROM regroup_undo_faces');c.execute('DELETE FROM regroup_undo_people')
  c.execute('INSERT INTO regroup_undo_faces SELECT id,person FROM faces');c.execute('INSERT INTO regroup_undo_people SELECT id,name FROM people')
  original_conflicts={r[0] for r in c.execute('SELECT photo FROM faces GROUP BY photo,person HAVING count(*)>1')}
  c.executemany('UPDATE faces SET person=? WHERE person=?',[(dest,int(src)) for src,dest in plan['mapping'].items() if int(src)!=dest])
  # Destinations are surviving original group IDs, so updates cannot cascade through removed IDs.
  c.execute('DELETE FROM people WHERE id NOT IN (SELECT DISTINCT person FROM faces)')
  conflicts={r[0] for r in c.execute('SELECT photo FROM faces GROUP BY photo,person HAVING count(*)>1')}
  if conflicts-original_conflicts:raise ValueError('Regrouping introduced same-photo conflicts.')
  if c.execute('SELECT count(*) FROM faces').fetchone()[0]!=plan['faces']:raise ValueError('Face count changed.')
  c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('regroup_undo_fingerprint',fingerprint(c)))
  c.commit()
 except:c.rollback();raise
 return {'message':f"Regrouped {plan['before']:,} groups into {plan['after']:,}. Review suggested matches. Undo is available until the next edit or scan."}

def undo(c):
 c.execute('BEGIN IMMEDIATE')
 try:
  row=c.execute("SELECT value FROM settings WHERE key='regroup_undo_fingerprint'").fetchone()
  if not row:raise ValueError('No regrouping to undo.')
  if fingerprint(c)!=row[0]:raise ValueError('The catalog has changed. Undo is disabled to preserve subsequent edits. The original backup remains available.')
  c.execute('DELETE FROM people');c.execute('INSERT INTO people SELECT * FROM regroup_undo_people')
  c.execute('UPDATE faces SET person=(SELECT person FROM regroup_undo_faces WHERE id=faces.id)')
  c.execute("DELETE FROM settings WHERE key='regroup_undo_fingerprint'");c.commit()
 except:c.rollback();raise
 return {'message':'Restored groups and names from before regrouping.'}

def review_pairs(c,only_different=False):
 query="SELECT a.person,b.person,d.decision FROM review_decisions d JOIN faces a ON a.id=d.face_a JOIN faces b ON b.id=d.face_b"
 return {tuple(sorted((r[0],r[1]))) for r in c.execute(query) if r[0]!=r[1] and (not only_different or r[2]=='different')}

def suggestions(c,with_counts=False):
 blocked=review_pairs(c)
 rows=c.execute('SELECT person,photo,embedding,thumb FROM faces ORDER BY id').fetchall()
 grouped={}
 for r in rows:grouped.setdefault(r['person'],[]).append(r)
 names={r['id']:r['name'] for r in c.execute('SELECT id,name FROM people')}
 ids=list(grouped);centers=[];photos=[];thumbs=[]
 for pid in ids:
  group=grouped[pid];v=np.stack([np.frombuffer(r['embedding'],np.float32) for r in group]);center=v.mean(axis=0);center/=max(np.linalg.norm(center),1e-8);centers.append(center)
  photos.append({r['photo'] for r in group});order=np.argsort(v@center)[::-1];thumbs.append([group[int(i)]['thumb'] for i in order[:3]])
 if not ids:return {'items':[],'remaining':0} if with_counts else []
 positions={pid:i for i,pid in enumerate(ids)};blocked_positions={}
 for left,right in blocked:
  if left in positions and right in positions:
   i,j=positions[left],positions[right];blocked_positions.setdefault(i,[]).append(j);blocked_positions.setdefault(j,[]).append(i)
 mat=np.stack(centers);candidates=[]
 for start in range(0,len(ids),256):
  sim=mat[start:start+256]@mat.T
  for local,row in enumerate(sim):
   i=start+local;row[:i+1]=-2
   # Exclude reviewed pairs BEFORE ranking, so later candidates can enter the queue.
   row[blocked_positions.get(i,[])]=-2
   near=np.flatnonzero(row>=0.52)
   for j in near:
    if row[j]<0.52 or (names[ids[i]] and names[ids[j]]) or photos[i]&photos[j]:continue
    candidates.append((float(row[j]),i,int(j)))
 candidates.sort(reverse=True);out=[];used=set()
 for score,i,j in candidates:
  if ids[i] in used or ids[j] in used:continue
  if names[ids[j]] or (not names[ids[i]] and len(photos[j])>len(photos[i])):i,j=j,i
  out.append({'id':f'{ids[i]}-{ids[j]}','keep':ids[i],'source':ids[j],'leftName':names[ids[i]] or f'Person {ids[i]}','rightName':names[ids[j]] or f'Person {ids[j]}','leftCount':len(photos[i]),'rightCount':len(photos[j]),'leftThumbs':thumbs[i],'rightThumbs':thumbs[j]})
  used.update((ids[i],ids[j]))
  if len(out)>=40:break
 return {'items':out,'remaining':len(candidates)} if with_counts else out

if __name__=='__main__':
 c=engine.db()
 try:
  if engine.summary(c).get('phase')=='scanning':raise ValueError('Wait for the scan to finish.')
  command=sys.argv[1]
  if command=='plan':plan(c)
  elif command=='apply':print(json.dumps(apply(c)))
  elif command=='undo':print(json.dumps(undo(c)))
 finally:c.close()
