import importlib.util
import json
from pathlib import Path
import struct
import contextlib
import shutil
import uuid
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('music_manager',Path(__file__).with_name('music_manager.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


@contextlib.contextmanager
def test_folder():
    root=Path(__file__).resolve().parent/'.test-work'
    root.mkdir(parents=True,exist_ok=True)
    p=root/uuid.uuid4().hex
    p.mkdir()
    try:yield p
    finally:
        assert p.resolve().parent==root.resolve()
        shutil.rmtree(p)


def fixture(folder, entries):
    folder.mkdir(parents=True,exist_ok=True)
    positions=[];payload=bytearray()
    for key,value in entries:
        positions.append(len(payload)+len(value)%16)
        payload+=value+bytes(-len(value)%16)
    positions.append(len(payload))
    def chunk(tag,data):return struct.pack('>I4s',len(data)+8,tag)+data
    ofs=chunk(b'ofsi',b''.join(struct.pack('>II',key,0x11223344) for key,value in entries))
    ofs+=chunk(b'ofsd',struct.pack('>'+str(len(positions))+'I',*positions))
    (folder/'data000.ofs').write_bytes(ofs)
    (folder/'data000.bin').write_bytes(bytes(256)+payload)


def read_entries(folder):
    d=(folder/'data000.bin').read_bytes()
    return [(key,d[256+off:256+off+n]) for key,_,off,n in m.parse_index((folder/'data000.ofs').read_bytes())]


class MusicTests(unittest.TestCase):
    def test_rebuild_preserves_unrelated_entries_and_all_tail_lengths(self):
        with test_folder() as tmp:
            p=Path(tmp);fixture(p,[(1,b'a'*16),(2,b'b'*31),(3,b'c')])
            for n in [1,15,16,17,32,47]:
                m.rebuild_archive(p/'data000.bin',p/'data000.ofs',p/'new',{2:b'x'*n})
                rows=m.parse_index((p/'new.ofs').read_bytes());data=(p/'new.bin').read_bytes()
                self.assertEqual([(key,data[256+off:256+off+size]) for key,_,off,size in rows],[(1,b'a'*16),(2,b'x'*n),(3,b'c')])
                self.assertTrue(all(word==0x11223344 for _,word,_,_ in rows))

    def test_multiple_files_are_rejected(self):
        with test_folder() as tmp:
            p=Path(tmp);(p/'a.wav').touch();(p/'b.mp3').touch()
            with self.assertRaisesRegex(ValueError,'ONE audio file'):m.selected_track(p)

    def test_idsp_loop_and_surround_channel_count(self):
        frames=bytes([0x12,1,2,3,4,5,6,7])*3
        audio=m.make_idsp(frames,frames,[0]*16,29,6)
        self.assertEqual(struct.unpack_from('>5I',audio,8),(6,48000,29,0,29))
        self.assertEqual(struct.unpack_from('>H',audio,0x40+12)[0],1)
        preview=m.make_idsp(frames,frames,[0]*16,29,2,False)
        self.assertEqual(struct.unpack_from('>H',preview,0x40+12)[0],0)

    def setup_restore(self,p):
        game=p/'game';hdd=game/'content/hdd'
        fixture(hdd,[(1,b'custom music'),(2,b'an unrelated mod')])
        music=p/'music';originals=music/'.state/original-banks';originals.mkdir(parents=True)
        (originals/'00000001.nus3bank').write_bytes(b'original music')
        state={'00000001':dict(original_hash=m.digest(b'original music'),installed_hash=m.digest(b'custom music'),folders=['Stages/Example'])}
        m.write_json(music/'.state/assignments.json',state)
        return game,hdd,music,state

    def test_empty_folder_restore_keeps_unrelated_mod(self):
        with test_folder() as tmp:
            game,hdd,music,state=self.setup_restore(Path(tmp))
            m.apply_music(game,music,Path(tmp),[])
            self.assertEqual(read_entries(hdd),[(1,b'original music'),(2,b'an unrelated mod')])
            self.assertEqual(json.loads((music/'.state/assignments.json').read_text()),{})

    def test_external_changes_are_not_overwritten(self):
        with test_folder() as tmp:
            game,hdd,music,state=self.setup_restore(Path(tmp))
            fixture(hdd,[(1,b'external music change'),(2,b'an unrelated mod')])
            before=(hdd/'data000.bin').read_bytes()
            with self.assertRaisesRegex(ValueError,'another tool'):m.apply_music(game,music,Path(tmp),[])
            self.assertEqual((hdd/'data000.bin').read_bytes(),before)

    def test_mid_install_failure_restores_both_files_and_state(self):
        with test_folder() as tmp:
            game,hdd,music,state=self.setup_restore(Path(tmp));before={x.name:x.read_bytes() for x in hdd.iterdir()}
            replace=m.os.replace
            def fail_index(src,dst):
                if str(dst).endswith('data000.ofs'):raise OSError('simulated failed index publish')
                return replace(src,dst)
            with patch.object(m.os,'replace',side_effect=fail_index):
                with self.assertRaisesRegex(OSError,'simulated'):m.apply_music(game,music,Path(tmp),[])
            self.assertEqual({x.name:x.read_bytes() for x in hdd.iterdir() if not x.name.endswith('.custom-music-new')},before)
            self.assertEqual(json.loads((music/'.state/assignments.json').read_text()),state)
            self.assertFalse((music/'.state/pending.json').exists())

if __name__=='__main__':unittest.main()
