# Tag2Tunes

Tag2Tunes is a standalone Windows soundtrack tool for Tekken Tag 2 Ultimate Edition. Assign your own tracks to stages and menus before launching the game. Tag2Tunes reads your existing Wii U EU v16 game files and updates the affected sound banks. The download contains tool source only. Supply your own game and music.

## Setup

- Use Python 3.11 or later.
- Install FFmpeg and make sure `ffmpeg` is on PATH, or put `ffmpeg.exe` beside `music_manager.py`.
- Build the DSP encoder with a Windows C++17 compiler. Run one of these commands from the Tag2Tunes folder:

  ```powershell
  # Visual Studio Developer PowerShell
  cl /nologo /std:c++17 /O2 /MT /EHsc encode_dsp.cpp

  # Or LLVM with the Windows C++ toolchain installed
  clang++ -std=c++17 -O2 encode_dsp.cpp -o encode_dsp.exe
  ```

Keep `encode_dsp.exe` beside `music_manager.py`.

## Assign tracks

1. Close Ultimate Edition. Set `$game` to the extracted game directory that contains both `code/Tekken.rpx` and `content/hdd`.

   ```powershell
   $game = 'C:\Games\Tag2Ultimate\game'
   py -3 music_manager.py --game "$game" --init
   ```

2. Put one WAV, MP3, OGG, FLAC, or M4A in each stage or menu folder you want to change under `$game\Custom Music`. Leave the other folders empty.
3. Apply your assignments and launch Ultimate Edition normally.

   ```powershell
   py -3 music_manager.py --game "$game"
   ```

To restore one destination, remove its audio file and apply again. To restore all music managed by Tag2Tunes, run:

```powershell
py -3 music_manager.py --game "$game" --restore
```

Keep `Custom Music/.state` with the game. It holds the original banks and tracks what Tag2Tunes changed. If you already use the folder-based music manager, point Tag2Tunes at the same game and music folders so it can reuse that state. Pass `--music "path to the existing folder"` if you keep it elsewhere.

If an update is interrupted, close the game and run `py -3 music_manager.py --game "$game" --recover`. If the process was terminated and left `.music-lock` in your music folder, confirm the tool has exited before deleting the lock and running recovery.

Tag2Tunes supports the base game's mapped stage and menu banks. All music phases on a stage share the assigned track. Existing in-game Tekken Tunes assignments can bypass a destination's default bank. Imported stages and per-round assignments are outside the current feature set.

## Tests

```powershell
py -3 -m unittest -v
```

The tests use synthetic archives and cover archive preservation, looping metadata, restoration, and transaction rollback.
