"""生成 hwprint\\sounds 里的语音提醒：叮咚 + 微软云希念一句话，存成 WAV（winsound 能直接播）。
只在要改说法时运行一次；程序运行时不需要联网。
需要 ffmpeg（winget install ffmpeg）和 edge-tts：没有的话先
    python -m pip install edge-tts --target tools\\pylib_tts
"""
import asyncio
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'pylib_tts'))
import edge_tts

OUT = os.path.join(os.path.dirname(HERE), 'hwprint', 'sounds')
VOICE = 'zh-CN-YunxiNeural'
PHRASES = {
    '换页': '本页已打完，请您更换空白页',
    '翻页': '本页已打完，请您翻页',                 # 下一页是偶数页：写在这一张的背面（09-28 加）
    '换首页': '本份已打完，请您更换带表格的首页',
    '换会议记录本': '本份已打完，请您换成会议记录本的首页',
    '换政治理论学习记录本': '本份已打完，请您换成政治理论学习记录本的首页',
    '全部完成': '全部已打完',
    '出错': '写字机出错停下了，请您过来看一下',
}
TMP = os.path.join(tempfile.gettempdir(), 'hwprint_voice_tmp')
os.makedirs(TMP, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

# 叮咚：两个带衰减的正弦音，后面留 0.25 秒空
chime = os.path.join(TMP, 'chime.wav')
subprocess.run(['ffmpeg', '-y', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'sine=frequency=988:duration=0.35',
                '-f', 'lavfi', '-i', 'sine=frequency=784:duration=0.6',
                '-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono:d=0.25',
                '-filter_complex',
                '[0]afade=t=out:st=0.05:d=0.3,volume=0.5,aresample=24000[a];'
                '[1]afade=t=out:st=0.05:d=0.55,volume=0.5,aresample=24000[b];'
                '[a][b][2]concat=n=3:v=0:a=1,aformat=sample_fmts=s16:channel_layouts=mono[o]',
                '-map', '[o]', chime], check=True)


async def tts(text, path):
    await edge_tts.Communicate(text, VOICE, rate='-5%').save(path)


only = sys.argv[1:]                     # 只生成指定的几句，如 python tools\make_voices.py 翻页；不写就全部重新生成
for name, text in PHRASES.items():
    if only and name not in only:
        continue
    mp3 = os.path.join(TMP, name + '.mp3')
    asyncio.run(tts(text, mp3))
    out = os.path.join(OUT, name + '.wav')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', chime, '-i', mp3, '-filter_complex',
                    '[1]aresample=24000,aformat=sample_fmts=s16:channel_layouts=mono,loudnorm=I=-16:TP=-1.5[v];'
                    '[0][v]concat=n=2:v=0:a=1[o]',
                    '-map', '[o]', '-ar', '24000', '-ac', '1', '-c:a', 'pcm_s16le', out], check=True)
    print('%-12s %s' % (name, text))
