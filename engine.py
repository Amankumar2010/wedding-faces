"""Offline wedding photo catalog. Originals are only opened read-only."""
import argparse, contextlib, hashlib, io, json, os, re, shutil, sqlite3, sys, time, traceback
from pathlib import Path
BASE=Path(__file__).resolve().parent
DATA=Path(os.environ.get('WEDDING_FACES_DATA',str(BASE/'Library')))
DATA.mkdir(parents=True,exist_ok=True)
(DATA/'thumbs').mkdir(exist_ok=True)
def db():
 c=sqlite3.connect(DATA/'catalog.sqlite',timeout=30); c.row_factory=sqlite3.Row
 c.executescript('''PRAGMA journal_mode=WAL;
 CREATE TABLE IF NOT EXISTS photos(id INTEGER PRIMARY KEY, key TEXT UNIQUE, jpg TEXT, raw TEXT, thumb TEXT, error TEXT);
 CREATE TABLE IF NOT EXISTS people(id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '');
 CREATE TABLE IF NOT EXISTS faces(id INTEGER PRIMARY KEY, photo INTEGER REFERENCES photos(id), person INTEGER REFERENCES people(id), embedding BLOB, thumb TEXT, score REAL);
 CREATE INDEX IF NOT EXISTS person_idx ON faces(person);
 CREATE TABLE IF NOT EXISTS review_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, face_a INTEGER NOT NULL, face_b INTEGER NOT NULL, decision TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
 '''); return c
def state(**kw):
 p=DATA/'status.json'; tmp=DATA/f'status.{os.getpid()}.tmp';tmp.write_text(json.dumps(kw));tmp.replace(p)
def status():
 try:return json.loads((DATA/'status.json').read_text())
 except:return {'phase':'ready','message':'Choose your Wedding folder to begin.'}
def inventory(root):
 root=Path(root).resolve()
 if not root.is_dir():raise ValueError('Photo folder is unavailable. Reconnect the SSD and try again.')
 for folder,dirs,files in os.walk(root,onerror=lambda e: (_ for _ in ()).throw(e)):
  dirs[:]=sorted(d for d in dirs if not d.startswith('.'))
  pairs={}
  for f in sorted(files):
   p=Path(folder)/f;e=p.suffix.lower()
   if f.startswith('.') or e not in ('.jpg','.jpeg','.arw'):continue
   key=str(p.with_suffix(''))
   pair=pairs.setdefault(key,{'key':key,'jpg':None,'raw':None})
   pair['raw' if e=='.arw' else 'jpg']=str(p)
  yield from pairs.values()
def decode(pair):
 import numpy as np
 from PIL import Image,ImageOps
 if pair['jpg']:
  try:
   with Image.open(pair['jpg']) as im:
    im.draft('RGB',(1800,1800)); im=ImageOps.exif_transpose(im);im.thumbnail((1800,1800));return np.array(im.convert('RGB'))[:,:,::-1].copy()
  except Exception:
   if not pair['raw']:raise
 import rawpy
 with rawpy.imread(pair['raw']) as raw:
  try:
   t=raw.extract_thumb()
   im=Image.open(io.BytesIO(t.data)) if t.format==rawpy.ThumbFormat.JPEG else Image.fromarray(t.data)
   im=ImageOps.exif_transpose(im);im.thumbnail((1800,1800));return np.array(im.convert('RGB'))[:,:,::-1].copy()
  except Exception:
   a=raw.postprocess(half_size=True,use_camera_wb=True,output_bps=8)
   im=Image.fromarray(a);im.thumbnail((1800,1800));return np.array(im)[:,:,::-1].copy()
def save_thumb(image,name,size):
 import cv2
 h,w=image.shape[:2]; out=DATA/'thumbs'/f'{name}.jpg'
 image=cv2.resize(image,(max(1,int(w*min(1,size/w,size/h))),max(1,int(h*min(1,size/w,size/h)))))
 if not cv2.imwrite(str(out),image,[cv2.IMWRITE_JPEG_QUALITY,83]):raise IOError('Unable to save thumbnail')
 return str(out)
def scan(root,limit):
 root=str(Path(root).resolve())
 import fcntl
 lock=open(DATA/'scan.lock','w')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise ValueError('A scan is already running.')
 (DATA/'pause').unlink(missing_ok=True)
 import cv2,numpy as np
 cv2.setNumThreads(2)
 c=db();c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('root',root));c.commit()
 started=time.monotonic(); done=0; skipped=0; errors=0; total=0
 try:
  state(phase='scanning',message='Finding JPG / ARW pairs…',done=0,total=0)
  # Metadata only. Pixels are decoded one photo at a time.
  pairs=list(inventory(root));total=len(pairs)
  if limit:
   from collections import defaultdict,deque
   buckets=defaultdict(deque)
   for pair in pairs:buckets[str(Path(pair['key']).parent)].append(pair)
   pairs=[]
   while buckets:
    for folder in list(buckets):
     pairs.append(buckets[folder].popleft())
     if not buckets[folder]:del buckets[folder]
  detector=cv2.FaceDetectorYN.create(str(BASE/'models/yunet.onnx'),'',(320,320),0.85,0.3,5000)
  recognizer=cv2.FaceRecognizerSF.create(str(BASE/'models/sface.onnx'),'')
  sums={};counts={}
  for row in c.execute('SELECT person,embedding FROM faces'):
   v=np.frombuffer(row['embedding'],dtype=np.float32).copy();p=row['person'];sums[p]=sums.get(p,0)+v;counts[p]=counts.get(p,0)+1
  def centers():
   ids=list(sums);mat=np.stack([sums[i]/max(np.linalg.norm(sums[i]),1e-8) for i in ids]) if ids else np.empty((0,128),np.float32);return ids,mat
  ids,mat=centers()
  for pair in pairs:
   if (DATA/'pause').exists():break
   old=c.execute('SELECT id,error FROM photos WHERE key=?',(pair['key'],)).fetchone()
   if old and old['error'] is None:
    skipped+=1;continue
   if limit and done>=limit:break
   if not Path(root).is_dir():raise ValueError('SSD disconnected. Reconnect it and resume.')
   state(phase='scanning',done=done,total=total,skipped=skipped,errors=errors,seconds=round(time.monotonic()-started,1),message=str(Path(pair['key']).relative_to(root)))
   try:
    img=decode(pair);h,w=img.shape[:2];detector.setInputSize((w,h));_,found=detector.detect(img)
    key=hashlib.sha256(pair['key'].encode()).hexdigest()[:24]
    thumb=save_thumb(img,key,420)
    with c:
     if old:
      pid=old['id'];c.execute('UPDATE photos SET jpg=?,raw=?,thumb=?,error=NULL WHERE id=?',(pair['jpg'],pair['raw'],thumb,pid))
     else:pid=c.execute('INSERT INTO photos(key,jpg,raw,thumb) VALUES (?,?,?,?)',(pair['key'],pair['jpg'],pair['raw'],thumb)).lastrowid
     used=set()
     for idx,face in enumerate(found if found is not None else []):
      if min(face[2:4])<28:continue
      aligned=recognizer.alignCrop(img,face);vec=recognizer.feature(aligned).reshape(-1).astype(np.float32);vec/=max(np.linalg.norm(vec),1e-8)
      sims=mat@vec if len(ids) else np.array([]);order=np.argsort(sims)[::-1];person=None;score=0.
      if len(order):
       best=int(order[0]);score=float(sims[best]);second=float(sims[order[1]]) if len(order)>1 else -1
       # Conservative suggestions; similar relatives and uncertain matches form separate groups.
       if score>=0.48 and score-second>=0.06 and ids[best] not in used:person=ids[best]
      if person is None:person=c.execute('INSERT INTO people DEFAULT VALUES').lastrowid
      face_thumb=save_thumb(aligned,f'{key}_face_{idx}',112)
      c.execute('INSERT INTO faces(photo,person,embedding,thumb,score) VALUES (?,?,?,?,?)',(pid,person,vec.tobytes(),face_thumb,score))
      used.add(person);sums[person]=sums.get(person,0)+vec;counts[person]=counts.get(person,0)+1
      ids,mat=centers()
    done+=1
   except Exception as e:
    c.rollback();errors+=1;done+=1
    # Rebuild in-memory state after rollback, so failed work cannot affect later matches.
    sums={}
    for row in c.execute('SELECT person,embedding FROM faces'):
     v=np.frombuffer(row['embedding'],dtype=np.float32).copy();sums[row['person']]=sums.get(row['person'],0)+v
    ids,mat=centers()
    with c:c.execute('INSERT INTO photos(key,jpg,raw,error) VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET error=excluded.error',(pair['key'],pair['jpg'],pair['raw'],str(e)))
  phase='paused' if (DATA/'pause').exists() else 'complete'
  state(phase=phase,done=done,total=total,skipped=skipped,errors=errors,seconds=round(time.monotonic()-started,1),message=('Paused. Resume whenever you’re ready.' if phase=='paused' else ('Sample finished. Review the suggested groups, then scan all.' if limit else 'Scan finished. Review and name your face groups.')))
 except Exception as e:state(phase='error',message=str(e),done=done,total=total,errors=errors)
 finally:c.close();fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
def summary(c):
 s=status()
 # A terminated process should not leave the UI stuck in scanning state.
 if s.get('phase')=='scanning':
  import fcntl
  with open(DATA/'scan.lock','a') as f:
   try:
    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);s.update(phase='paused',message='Scan interrupted. Resume to continue.');fcntl.flock(f,fcntl.LOCK_UN)
   except BlockingIOError:pass
 s['photos']=c.execute('SELECT count(*) FROM photos WHERE error IS NULL').fetchone()[0]
 s['faces']=c.execute('SELECT count(*) FROM faces').fetchone()[0]
 s['failed']=c.execute('SELECT count(*) FROM photos WHERE error IS NOT NULL').fetchone()[0]
 row=c.execute("SELECT value FROM settings WHERE key='root'").fetchone();s['root']=row[0] if row else ''
 s['people']=[dict(r) for r in c.execute("SELECT p.id,p.name,count(DISTINCT f.photo) AS count,min(f.thumb) AS thumb FROM people p JOIN faces f ON f.person=p.id GROUP BY p.id ORDER BY count DESC,p.id")]
 return s
def export(c,person,dest,mode):
 root=c.execute("SELECT value FROM settings WHERE key='root'").fetchone()
 if not root:raise ValueError('No photo folder selected')
 source=Path(root[0]).resolve();dest=Path(dest).resolve()
 if dest==source or source in dest.parents:raise ValueError('Choose an export folder outside the original Wedding folder.')
 personrow=c.execute('SELECT name FROM people WHERE id=?',(person,)).fetchone()
 if not personrow:raise ValueError('Person not found')
 name=re.sub(r'[^\w .-]','_',personrow[0]).strip(' .') or f'Person {person}'
 out=dest/f'{name} - {person}';todo=[]
 for row in c.execute('SELECT DISTINCT p.* FROM photos p JOIN faces f ON f.photo=p.id WHERE f.person=?',(person,)):
  for kind in (['jpg','raw'] if mode=='both' else [mode]):
   if row[kind]:
    p=Path(row[kind]);target=out/p.relative_to(source)
    if target.exists():raise ValueError(f'Export already contains {target.name}. Choose a new destination to avoid overwriting.')
    todo.append((p,target))
 if not todo:raise ValueError('No files of that format in this group.')
 needed=sum(p.stat().st_size for p,_ in todo)
 if shutil.disk_usage(dest).free<needed+100_000_000:raise ValueError('Not enough free space for this export.')
 copied=0
 for p,t in todo:
  t.parent.mkdir(parents=True,exist_ok=True)
  created=False
  try:
   with p.open('rb') as src:
    with t.open('xb') as dst:
     created=True;shutil.copyfileobj(src,dst,1024*1024)
  except Exception:
   if created:t.unlink(missing_ok=True)
   raise
  copied+=1
 return {'message':f'Copied {copied} files to {out}','path':str(out)}
def main():
 a=argparse.ArgumentParser();a.add_argument('command');a.add_argument('args',nargs='*');ns=a.parse_args();cmd=ns.command;x=ns.args;c=db()
 if cmd=='scan':c.close();scan(x[0],int(x[1]));return
 if cmd=='summary':result=summary(c)
 elif cmd in ('review-decision','review-undo','review-reset-skipped'):
  if summary(c).get('phase')=='scanning':raise ValueError('Pause the scan before reviewing groups.')
  with c:
   if cmd=='review-decision':
    left,right=map(int,x[:2]);decision=x[2]
    if decision not in ('skipped','different') or left==right:raise ValueError('Invalid review decision.')
    a=c.execute('SELECT min(id) FROM faces WHERE person=?',(left,)).fetchone()[0]
    b=c.execute('SELECT min(id) FROM faces WHERE person=?',(right,)).fetchone()[0]
    if a is None or b is None:raise ValueError('Groups changed. Refresh suggestions.')
    c.execute('INSERT INTO review_decisions(face_a,face_b,decision) VALUES (?,?,?)',(a,b,decision))
   elif cmd=='review-undo':c.execute('DELETE FROM review_decisions WHERE id=(SELECT max(id) FROM review_decisions)')
   else:c.execute("DELETE FROM review_decisions WHERE decision='skipped'")
  result={'ok':True}
 elif cmd in ('suggestions','review-summary'):
  import regroup
  result=regroup.suggestions(c,with_counts=cmd=='review-summary')
 elif cmd in ('smart-regroup','batch-summary','merge-batch'):
  import smart_groups
  if summary(c).get('phase')=='scanning':raise ValueError('Wait for the scan to finish.')
  if cmd=='smart-regroup':result=smart_groups.improve(c)
  elif cmd=='batch-summary':result=smart_groups.batch_summary(c)
  else:result=smart_groups.merge_batch(c,int(x[0]),[int(p) for p in x[1:]])
 elif cmd=='undo-regroup':
  import regroup
  if summary(c).get('phase')=='scanning':raise ValueError('Wait for the scan to finish.')
  result=regroup.undo(c)
 elif cmd=='preview':
  row=c.execute('SELECT p.* FROM photos p JOIN faces f ON f.photo=p.id WHERE f.id=?',(int(x[0]),)).fetchone()
  if not row:raise ValueError('Photo not found in the catalog.')
  if not any(row[k] and Path(row[k]).is_file() for k in ('jpg','raw')):
   raise ValueError('Original photo unavailable. Reconnect the SSD and try again.')
  img=decode(dict(row))
  result={'path':save_thumb(img,'viewer-preview',1800)}
 elif cmd=='pause':(DATA/'pause').touch();result={'ok':True}
 elif cmd=='photos':
  result=[dict(r) for r in c.execute('SELECT f.id,f.thumb AS face,p.thumb,p.jpg,p.raw,p.key FROM faces f JOIN photos p ON p.id=f.photo WHERE f.person=? ORDER BY p.key LIMIT 300 OFFSET ?',(int(x[0]),int(x[1]) if len(x)>1 else 0))]
 elif cmd in ('rename','merge','detach'):
  if summary(c).get('phase')=='scanning':raise ValueError('Pause the scan before editing groups.')
  with c:
   if cmd=='rename':c.execute('UPDATE people SET name=? WHERE id=?',(x[1][:100],int(x[0])))
   elif cmd=='merge':
    src,dst=map(int,x[:2])
    if src==dst or not c.execute('SELECT 1 FROM people WHERE id=?',(dst,)).fetchone():raise ValueError('Choose another existing group.')
    import regroup
    if tuple(sorted((src,dst))) in regroup.review_pairs(c,only_different=True):raise ValueError('You marked these groups as different people. Undo that review decision before merging.')
    c.execute('UPDATE faces SET person=? WHERE person=?',(dst,src));c.execute('DELETE FROM people WHERE id=?',(src,))
   else:
    old=c.execute('SELECT person FROM faces WHERE id=?',(int(x[0]),)).fetchone()
    if not old:raise ValueError('Face not found.')
    pid=c.execute('INSERT INTO people DEFAULT VALUES').lastrowid;c.execute('UPDATE faces SET person=? WHERE id=?',(pid,int(x[0])))
    c.execute('CREATE TABLE IF NOT EXISTS regroup_protected(person INTEGER PRIMARY KEY)')
    c.executemany('INSERT OR IGNORE INTO regroup_protected VALUES (?)',[(pid,),(old[0],)])
  result={'ok':True}
 elif cmd=='export':result=export(c,int(x[0]),x[1],x[2])
 elif cmd=='errors':result=[dict(r) for r in c.execute('SELECT key,error FROM photos WHERE error IS NOT NULL LIMIT 100')]
 else:raise ValueError('Unknown command')
 print(json.dumps(result))
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'error':str(e)}));sys.exit(1)
