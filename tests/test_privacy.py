import importlib.util,os,subprocess,sys,tempfile,unittest,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class PrivacyTests(unittest.TestCase):
 def test_fresh_catalog_is_empty(self):
  with tempfile.TemporaryDirectory() as tmp:
   env=dict(os.environ,WEDDING_FACES_DATA=str(Path(tmp)/'library'))
   result=subprocess.run([sys.executable,str(ROOT/'engine.py'),'summary'],env=env,capture_output=True,text=True,check=True)
   summary=json.loads(result.stdout)
   self.assertEqual(summary['photos'],0)
   self.assertEqual(summary['faces'],0)
   self.assertEqual(summary['people'],[])
   self.assertFalse(summary.get('root'))
 def test_manifest_contains_no_runtime_data(self):
  spec=importlib.util.spec_from_file_location('packager',ROOT/'scripts/package_source.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  files=module.reviewed_files()
  self.assertTrue(files)
  for name in files:
   self.assertFalse(set(Path(name).parts)&{'Library','.venv','work','.git','build'})
   self.assertNotIn(Path(name).suffix.lower(),{'.jpg','.arw','.png','.sqlite','.npz','.onnx'})
 def test_pair_inventory_keeps_folders_separate(self):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp)
   for folder in ['one','two']:
    (base/folder).mkdir()
    for ext in ['JPG','ARW']:(base/folder/('synthetic.'+ext)).write_bytes(b'synthetic fixture')
   script='import engine,json; print(json.dumps(list(engine.inventory(__import__("sys").argv[1]))))'
   result=subprocess.run([sys.executable,'-c',script,tmp],cwd=ROOT,env=dict(os.environ,WEDDING_FACES_DATA=str(base/'data')),capture_output=True,text=True,check=True)
   pairs=json.loads(result.stdout);self.assertEqual(len(pairs),2)
   self.assertTrue(all(p['jpg'] and p['raw'] for p in pairs))
if __name__=='__main__':unittest.main()
