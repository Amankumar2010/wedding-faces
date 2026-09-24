"""Download public OpenCV models only; verify bytes before installation."""
import hashlib,json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'models'
for model in json.loads((root/'manifest.json').read_text()):
 path=root/model['file']
 if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==model['sha256']:continue
 data=subprocess.run(['/usr/bin/curl','--fail','--silent','--show-error','--location','--max-time','180',model['url']],capture_output=True,check=True).stdout
 if hashlib.sha256(data).hexdigest()!=model['sha256']:raise RuntimeError('Model checksum mismatch: '+model['file'])
 path.write_bytes(data)
 print('Verified '+model['file'])
