"""RSIdemo research submission; attribution is in RSIdemo/REFERENCE_NOTES.md."""
import sys
from pathlib import Path
def _path():return _path.__code__.co_filename
root=Path(globals().get('__file__',_path())).resolve().parent
if str(root) not in sys.path:sys.path.insert(0,str(root))
from RSIdemo.agent import agent,main
if __name__=='__main__':main()
