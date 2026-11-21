"""Folder-based custom music for the Wii U PC port. No runtime patches required."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import wave

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ttt2char.ttt2.archive import Archives, parse_index
from ttt2char.ttt2.nus3bank import Bank
from ttt2char.ttt2.rpx import Rpx

VERSION = 1
EXTENSIONS = {'.wav', '.mp3', '.ogg', '.flac', '.m4a'}
STAGES = list(range(18)) + [25] + list(range(40, 49)) + [50]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.new')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def bank_name(bank):
    b = bank.chunk(b'BINF')
    return b[9:9+b[8]].rstrip(b'\0').decode('latin1')


def catalogue(game):
    rpx = Rpx.of_game(str(game))
    # Validate the exact native resolver/table layout before using it.
    if rpx.u32(0x0220185C) != 0x28000011 or rpx.u32(0x105b53e0) != 0x93ca7ca4:
        raise ValueError('Unsupported game executable: music mappings need verification')
    rows = []
    for sid in STAGES:
        pointer = (rpx.u32(0x10024D20+sid*4) if sid <= 17 else
                   0x10024CCC if sid == 25 else
                   rpx.u32(0x10024C3C+sid*4) if sid <= 48 else 0x10024D0C)
        name = rpx.read(pointer, 160).split(b'\0', 1)[0].decode('utf-8')
        if sid == 40:
            name = 'Fireworks Over Barcelona'
        if sid == 50:
            name = 'Majestic Theatre'  # Native debug name: Odeum of Illusions.
        # Read the stage-to-bank lookup, independently of display-name ordering.
        key = rpx.u32(0x105b53e0 + sid*4)
        rows.append(dict(folder='Stages/'+name, key=f'{key:08x}',
                         bank=f'rm_Stg_stg{sid:02d}', stage_id=sid,
                         tones=None, preview=False))
    # The offline-menu bank was established by manual playback. It is not stage 32.
    rows += [dict(folder='Menus/Main Menu', key='0eddca98', bank='rm_Stg_stg32',
                  tones=['stg32', 'stg32_51', 'stg32_tunes', 'stg32_tunes_51'], preview=True)]
    a = Archives(str(game))
    try:
        names = {}
        # Locate only the known menu bank names by their actual bank metadata.
        for archive, entries in a.indices.items():
            if archive not in {'data020', 'data021', 'data022', 'data023', 'data024'}:
                continue
            f = a._bin(archive)
            for i, (key, _, off, size) in enumerate(entries):
                f.seek(256+off)
                head = f.read(min(size, 512))
                if head[:4] != b'NUS3' or head[8:16] != b'BANKTOC ':
                    continue
                toc, count = struct.unpack_from('<II', head, 16)
                pos = 20+toc
                for j in range(count):
                    tag = head[24+8*j:28+8*j]
                    length = struct.unpack_from('<I', head, 28+8*j)[0]
                    if tag == b'BINF':
                        f.seek(256+off+pos+8)
                        b = f.read(length)
                        name = b[9:9+b[8]].rstrip(b'\0').decode('latin1')
                        names[name] = key
                        break
                    pos += 8+length
        for folder, name, tones in [
            ('Character Select', 'rm_Bgm_charasele', None),
            ('Online Menu', 'rm_Bgm_online', ['Online_menu', 'Online_menu_51']),
            ('Online Lounge', 'rm_Bgm_online', ['Online_lounge', 'Online_lounge_51']),
            ('Online Practice', 'rm_Bgm_online', ['Online_pra', 'Online_pra_51']),
        ]:
            if name in names:
                rows.append(dict(folder='Menus/'+folder, key=f'{names[name]:08x}',
                                 bank=name, tones=tones, preview=False))
        for row in rows:
            key = int(row['key'], 16)
            if key not in a.where:
                raise ValueError('Missing music bank: '+row['folder'])
    finally:
        a.close()
    return rows


def selected_track(folder):
    tracks = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS)
    if len(tracks) > 1:
        raise ValueError(f'{folder}: keep ONE audio file in this folder; found {len(tracks)}')
    return tracks[0] if tracks else None


def nibbles(n):
    return n//14*16 + (n % 14+2 if n % 14 else 0)


def make_idsp(left, right, coefs, samples, channels, loop=True):
    if channels not in (1, 2, 6):
        raise ValueError(f'Unsupported music channel count: {channels}')
    size = (samples+13)//14*8
    # Preserve stereo balance: surround playback uses only front L/R.
    streams = [left] if channels == 1 else [left, right]
    if channels == 6:
        streams += [bytes(size)]*4
    headers = []
    for data in streams:
        h = struct.pack('>IIIHHIII', samples, nibbles(samples), 48000, int(loop), 0,
                        2, nibbles(samples)-1, 2)
        h += struct.pack('>16h', *coefs)
        h += struct.pack('>7H', 0, data[0], 0, 0, data[0] if loop else 0, 0, 0)
        headers.append(h.ljust(0x60, b'\0'))
    head = struct.pack('>4sIIIIIIIIIII', b'IDSP', 0, channels, 48000, samples, 0,
                       samples if loop else 0, 16, 0x40, 0x60, 0x40+0x60*channels, size)
    body = bytearray()
    for pos in range(0, size, 16):
        for data in streams:
            body += data[pos:min(pos+16, size)].ljust(16, b'\0')
    return head.ljust(0x40, b'\0') + b''.join(headers) + body


def convert(track, donor, tools, cache):
    if donor[:4] != b'IDSP':
        raise ValueError('Expected native IDSP music donor')
    coefs = struct.unpack_from('>16h', donor, 0x5c)
    signature = digest(track.read_bytes()+struct.pack('>16h', *coefs)+str(VERSION).encode())
    work = cache / signature
    work.mkdir(exist_ok=True)
    encoded, metadata = work/'stereo.dsp', work/'audio.json'
    if not (encoded.is_file() and metadata.is_file()):
        print('Converting '+track.name, flush=True)
        wav = work/'stereo.wav'
        bundled_ffmpeg = tools/'ffmpeg.exe'
        ffmpeg = str(bundled_ffmpeg) if bundled_ffmpeg.is_file() else shutil.which('ffmpeg')
        if not ffmpeg:
            raise ValueError('Install FFmpeg on PATH or place ffmpeg.exe beside music_manager.py')
        encoder = tools/'encode_dsp.exe'
        if not encoder.is_file():
            raise ValueError('Build encode_dsp.exe as described in README.md and place it beside music_manager.py')
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-i', str(track),
                        '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(wav)], check=True)
        with wave.open(str(wav), 'rb') as stream:
            count = stream.getnframes()
            if not count:
                raise ValueError('Empty audio file: '+str(track))
            (work/'stereo.pcm').write_bytes(stream.readframes(count))
        (work/'coefs.bin').write_bytes(struct.pack('<16h', *coefs))
        subprocess.run([str(encoder), str(work/'stereo.pcm'),
                        str(work/'coefs.bin'), '2', str(encoded)], check=True)
        write_json(metadata, dict(samples=count))
        (work/'stereo.pcm').unlink()
        wav.unlink()
    count = json.loads(metadata.read_text())['samples']
    raw = encoded.read_bytes()
    size = (count+13)//14*8
    if len(raw) != size*2:
        raise ValueError('Incomplete conversion cache: '+str(work))
    return raw[:size], raw[size:], coefs, count


def rebuild_archive(source_bin, source_ofs, output, replacements):
    """Preserve every unselected entry and index metadata; replace stored bytes."""
    source_bin, source_ofs, output = map(Path, (source_bin, source_ofs, output))
    ofs = bytearray(source_ofs.read_bytes())
    rows = parse_index(ofs)
    unknown = set(replacements)-{row[0] for row in rows}
    if unknown:
        raise ValueError('Replacement keys missing from archive')
    newbin, newofs = output.with_suffix('.bin'), output.with_suffix('.ofs')
    offsets, pos = [], 0
    with source_bin.open('rb') as src, newbin.open('wb') as dst:
        header = src.read(256)
        if len(header) != 256:
            raise ValueError('Truncated archive')
        dst.write(header)
        for key, _, start, size in rows:
            src.seek(256+start)
            data = replacements[key] if key in replacements else src.read(size)
            if key not in replacements and len(data) != size:
                raise ValueError('Truncated archive entry')
            offsets.append(pos+len(data) % 16)
            padding = -len(data) % 16
            dst.write(data)
            dst.write(bytes(padding))
            pos += len(data)+padding
    offsets.append(pos)
    pos = 0
    while pos+8 <= len(ofs):
        length, tag = struct.unpack_from('>I4s', ofs, pos)
        if length < 8 or pos+length > len(ofs):
            raise ValueError('Malformed archive index')
        if tag == b'ofsd':
            if length-8 != len(offsets)*4:
                raise ValueError('Index length mismatch')
            struct.pack_into('>'+str(len(offsets))+'I', ofs, pos+8, *offsets)
            break
        pos += length
    else:
        raise ValueError('No offsets in archive index')
    newofs.write_bytes(ofs)
    newrows = parse_index(ofs)
    with source_bin.open('rb') as src, newbin.open('rb') as dst:
        for old, new in zip(rows, newrows):
            if old[:2] != new[:2]:
                raise ValueError('Archive identifiers changed')
            src.seek(256+old[2])
            dst.seek(256+new[2])
            expected = replacements[old[0]] if old[0] in replacements else src.read(old[3])
            if dst.read(new[3]) != expected:
                raise ValueError('Rebuilt archive verification failed')
    return newbin, newofs


def apply_music(game, music, tools, rows, restore=False):
    state_root = music/'.state'
    state_root.mkdir(exist_ok=True)
    cache = state_root/'audio'
    originals = state_root/'original-banks'
    for folder in [cache, originals]:
        folder.mkdir(exist_ok=True)
    state_path = state_root/'assignments.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    pending = state_root/'pending.json'
    if pending.exists():
        raise ValueError('An interrupted update needs recovery. Run --recover before applying again.')
    desired = {}
    for row in rows:
        track = None if restore else selected_track(music/row['folder'])
        if track:
            desired.setdefault(row['key'], []).append((row, track))
    a = Archives(str(game))
    changes, next_state = {}, {}
    try:
        for hexkey in sorted(set(state) | set(desired)):
            key = int(hexkey, 16)
            current = a.raw(key)
            current_hash = digest(current)
            old = state.get(hexkey)
            if old and current_hash != old['installed_hash']:
                raise ValueError(f'Bank {hexkey} was changed by another tool; no files overwritten')
            backup = originals/(hexkey+'.nus3bank')
            if old:
                base = backup.read_bytes()
                if digest(base) != old['original_hash']:
                    raise ValueError('Original bank backup failed verification: '+hexkey)
            else:
                base = current
            assignments = desired.get(hexkey, [])
            if assignments:
                fingerprint = digest(json.dumps([(r['folder'], file_hash(p)) for r,p in assignments],
                                                sort_keys=True).encode()+str(VERSION).encode())
                if old and fingerprint == old['fingerprint']:
                    next_state[hexkey] = old
                    continue
                b = Bank(base)
                touched = set()
                for row, track in assignments:
                    if bank_name(b) != row['bank']:
                        raise ValueError('Music mapping mismatch: '+row['folder'])
                    indices = [i for i in range(len(b.tones)) if b.audio[i] and
                               (row['tones'] is None or b.name(i) in row['tones'])]
                    if not indices:
                        raise ValueError('No playable music tones: '+row['folder'])
                    left,right,coefs,samples = convert(track,b.audio[indices[0]],tools,cache)
                    print(row['folder']+' <- '+track.name, flush=True)
                    for i in indices:
                        if i in touched:
                            raise ValueError('Two folders target the same music tone')
                        touched.add(i)
                        channels = b.format(i)[1]
                        preview = '_tunes' in (b.name(i) or '')
                        n = min(samples, 7*48000) if preview else samples
                        audio = make_idsp(left,right,coefs,n,channels,not preview)
                        b.replace(i,audio,48000,channels,n)
                replacement = b.write()
                check = Bank(replacement)
                if len(check.tones) != len(b.tones):
                    raise ValueError('Music tone count changed')
                if not old:
                    backup.write_bytes(base)
                next_state[hexkey] = dict(original_hash=digest(base), installed_hash=digest(replacement),
                                          fingerprint=fingerprint,
                                          folders=[r['folder'] for r,p in assignments],
                                          tracks=[p.name for r,p in assignments])
            else:
                replacement = base
                print('Restoring '+', '.join(old['folders']), flush=True)
            if replacement != current:
                archive, _ = a.where[key]
                changes.setdefault(archive, {})[key] = replacement
    finally:
        a.close()
    if not changes:
        print('Custom music is up to date.' if next_state else 'Original music active; no assignments.', flush=True)
        return
    # Prepare every archive first. Keep rollback copies until the new state is committed.
    transaction = state_root/'transaction'
    transaction.mkdir(exist_ok=True)
    entries = []
    for archive,replacements in changes.items():
        binary = game/'content/hdd'/(archive+'.bin')
        index = binary.with_suffix('.ofs')
        original_hashes = {ext:file_hash(binary.with_suffix('.'+ext)) for ext in ['bin','ofs']}
        rebuild_archive(binary,index,transaction/archive,replacements)
        for ext in ['bin','ofs']:
            target = binary.with_suffix('.'+ext)
            staged = transaction/(archive+'.'+ext)
            backup = transaction/(archive+'.'+ext+'.rollback')
            shutil.copy2(target,backup)
            if file_hash(backup) != original_hashes[ext] or file_hash(target) != original_hashes[ext]:
                raise ValueError('Game archives changed during preparation; stopped')
            entries.append(dict(target=str(target),staged=str(staged),backup=str(backup),
                                original_hash=original_hashes[ext],replacement_hash=file_hash(staged)))
    write_json(pending, dict(entries=entries, previous_state=state))
    try:
        for e in entries:
            if file_hash(e['target']) != e['original_hash']:
                raise ValueError('Game archive changed during installation')
            swap = Path(e['target']).with_name(Path(e['target']).name+'.custom-music-new')
            shutil.copy2(e['staged'],swap)
            if file_hash(swap) != e['replacement_hash']:
                raise ValueError('Staged archive failed verification')
            os.replace(swap,e['target'])
            if file_hash(e['target']) != e['replacement_hash']:
                raise ValueError('Installed archive failed verification')
        write_json(state_path,next_state)
        pending.unlink()
    except BaseException:
        # Keep pending.json until recovery also completes state rollback.
        recover(music)
        raise
    for e in entries:
        Path(e['backup']).unlink(missing_ok=True)
        Path(e['staged']).unlink(missing_ok=True)
    print('Applied. Start the game normally; changes take effect on restart.', flush=True)


def recover(music):
    root = music/'.state'
    pending = root/'pending.json'
    if not pending.exists():
        print('No interrupted update to recover.')
        return
    data = json.loads(pending.read_text())
    for e in data['entries']:
        if file_hash(e['target']) not in {e['original_hash'],e['replacement_hash']}:
            raise ValueError('Recovery target changed externally: '+e['target'])
        if file_hash(e['backup']) != e['original_hash']:
            raise ValueError('Rollback backup hash mismatch')
    for e in data['entries']:
        shutil.copy2(e['backup'],e['target'])
        if file_hash(e['target']) != e['original_hash']:
            raise ValueError('Recovery verification failed')
    write_json(root/'assignments.json',data['previous_state'])
    pending.unlink()
    print('Interrupted update rolled back; original assignments preserved.')


@contextlib.contextmanager
def exclusive(music):
    lock = music/'.music-lock'
    try:
        fd = os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:
        raise ValueError('Another music command is running, or a previous run left .music-lock')
    try:
        os.write(fd,str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game',type=Path,required=True)
    parser.add_argument('--music',type=Path)
    parser.add_argument('--init',action='store_true')
    parser.add_argument('--restore',action='store_true')
    parser.add_argument('--recover',action='store_true')
    args = parser.parse_args()
    game = args.game.resolve()
    music = (args.music or game/'Custom Music').resolve()
    music.mkdir(exist_ok=True)
    with exclusive(music):
        if args.recover:
            recover(music)
            return
        rows = catalogue(game)
        for row in rows:
            (music/row['folder']).mkdir(parents=True,exist_ok=True)
        write_json(music/'destinations.json',rows)
        if args.init:
            print(f'Created {len(rows)} destination folders in {music}')
            return
        if os.name == 'nt':
            # Refuse archive replacement while the regular game is running.
            process_list = subprocess.run(['tasklist','/FO','CSV','/NH'],capture_output=True,text=True,check=True)
            if re.search(r'"TTT2 Ultimate[^"\r\n]*\.exe"',process_list.stdout,re.I):
                raise ValueError('Close the game before applying custom music')
        apply_music(game,music,HERE,rows,args.restore)


if __name__ == '__main__':
    try:
        main()
    except (ValueError,KeyError,OSError,subprocess.CalledProcessError) as error:
        print('Custom Music: '+str(error),file=sys.stderr)
        sys.exit(1)
