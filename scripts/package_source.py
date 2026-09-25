"""Package only explicitly approved source files and checksum-pinned demo images. Never walk the working library."""
import hashlib,json,re,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def reviewed_files():
 names=json.loads((ROOT/'PUBLIC_FILES.json').read_text())
 if len(names)!=len(set(names)):raise ValueError('Duplicate public path')
 files={}
 images=json.loads((ROOT/'PUBLIC_IMAGES.json').read_text())
 for name in names:
  rel=Path(name)
  if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe public path')
  path=ROOT/rel
  if any(p.is_symlink() for p in [path,*path.parents]):raise ValueError('Symlinks are not permitted')
  if not path.is_file() or not path.resolve().is_relative_to(ROOT):raise ValueError('Missing public file')
  if name in images:
   data=path.read_bytes()
   if not data.startswith(b'\xff\xd8\xff') or hashlib.sha256(data).hexdigest()!=images[name]:raise ValueError('Unreviewed image bytes in '+name)
   files[name]=data
   continue
  if path.suffix not in ('.py','.swift','.sh','.md','.txt','.json','.svg','.yml','') and name!='.gitignore':raise ValueError('Unreviewed file type')
  data=path.read_bytes();text=data.decode('utf-8')
  # Split markers keep the scanner's own source free of literal private paths.
  forbidden=['/'+ 'Users/', '/'+ 'Volumes/', 'BEGIN '+ 'PRIVATE KEY', 'gh'+'p_','github'+'_pat_']
  if any(marker in text for marker in forbidden):raise ValueError('Sensitive marker in '+name)
  files[name]=data
 return files

def main():
 files=reviewed_files()
 archive=ROOT.parent/'wedding-faces-source.zip'
 with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
  for name,data in files.items():z.writestr('wedding-faces/'+name,data)
 report={'file_count':len(files),'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':{k:hashlib.sha256(v).hexdigest() for k,v in files.items()}}
 (ROOT.parent/'source-audit.json').write_text(json.dumps(report,indent=2)+'\n')
 print('Packaged',len(files),'reviewed source files. No runtime folders included.')
if __name__=='__main__':main()
